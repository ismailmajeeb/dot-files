#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (C) 2021 Andy Stewart
#
# Author:     Andy Stewart <lazycat.manatee@gmail.com>
# Maintainer: Andy Stewart <lazycat.manatee@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# NOTE
# QtWebEngine will throw error "ImportError: QtWebEngineWidgets must be imported before a QCoreApplication instance is created"
from PyQt6.QtWebEngineWidgets import QWebEngineView

from PyQt6 import QtCore
from PyQt6.QtGui import QColor
from PyQt6.QtCore import QDateTime, QUrl, Qt, QEventLoop
from PyQt6.QtNetwork import QNetworkCookie, QNetworkProxy, QNetworkProxyFactory
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings, QWebEngineProfile
from PyQt6.QtWidgets import QWidget, QApplication, QVBoxLayout
from epc.client import EPCClient
from epc.server import ThreadingEPCServer
import base64
import functools
import importlib
import os
import platform
import signal
import sys
import threading

class PostGui(QtCore.QObject):

    through_thread = QtCore.pyqtSignal(object, object)

    def __init__(self, inclass=True):
        super(PostGui, self).__init__()
        self.through_thread.connect(self.on_signal_received)
        self.inclass = inclass

    def __call__(self, func):
        self._func = func

        @functools.wraps(func)
        def obj_call(*args, **kwargs):
            self.emit_signal(args, kwargs)
        return obj_call

    def emit_signal(self, args, kwargs):
        self.through_thread.emit(args, kwargs)

    def on_signal_received(self, args, kwargs):
        if self.inclass:
            obj, args = args[0], args[1:]
            self._func(obj, *args, **kwargs)
        else:
            self._func(*args, **kwargs)

epc_client = None

def init_epc_client(emacs_server_port):
    global epc_client

    if epc_client is None:
        try:
            epc_client = EPCClient(("127.0.0.1", emacs_server_port), log_traceback=True)
        except ConnectionRefusedError:
            import traceback
            traceback.print_exc()

def close_epc_client():
    global epc_client

    if epc_client is not None:
        epc_client.close()

def convert_arg_to_str(arg):
    if type(arg) == str:
        return arg
    elif type(arg) == bool:
        arg = str(arg).upper()
    elif type(arg) == list:
        new_arg = ""
        for a in arg:
            new_arg = new_arg + " " + convert_arg_to_str(a)
        arg = "(" + new_arg[1:] + ")"
    return arg

def string_to_base64(text):
    return str(base64.b64encode(str(text).encode("utf-8")), "utf-8")

def eval_in_emacs(method_name, args):
    global epc_client

    if epc_client is None:
        print("Please call init_epc_client first before callling eval_in_emacs.")
    else:
        args = list(map(convert_arg_to_str, args))
        # Make argument encode with Base64, avoid string quote problem pass to elisp side.
        args = list(map(string_to_base64, args))

        args.insert(0, method_name)

        # Call eval-in-emacs elisp function.
        epc_client.call("eval-in-emacs", args)


def convert_emacs_bool(symbol_value, symbol_is_boolean):
    if symbol_is_boolean == "t":
        return symbol_value is True
    else:
        return symbol_value

def get_emacs_vars(args):
    global epc_client

    return list(map(lambda result: convert_emacs_bool(result[0], result[1]) if result != [] else False, epc_client.call_sync("get-emacs-vars", args)))

def get_emacs_var(var_name):
    global epc_client

    (symbol_value, symbol_is_boolean) = epc_client.call_sync("get-emacs-var", [var_name])

    return convert_emacs_bool(symbol_value, symbol_is_boolean)

def get_emacs_func_result(method_name, args):
    global epc_client

    if epc_client is None:
        print("Please call init_epc_client first before callling eval_in_emacs.")
    else:
        args = list(map(convert_arg_to_str, args))
        # Make argument encode with Base64, avoid string quote problem pass to elisp side.
        args = list(map(string_to_base64, args))

        args.insert(0, method_name)

        # Call eval-in-emacs elisp function synchronously and return the result
        result = epc_client.call_sync("eval-in-emacs", args)
        return result if result != [] else False

emacs_config_dir = ""

def get_emacs_config_dir():
    import os

    global emacs_config_dir

    if emacs_config_dir == "":
        emacs_config_dir = os.path.join(os.path.expanduser(get_emacs_var("popweb-config-location")), '')

    if not os.path.exists(emacs_config_dir):
        os.makedirs(emacs_config_dir)

    return emacs_config_dir

def touch(path):
    import os

    if not os.path.exists(path):
        basedir = os.path.dirname(path)

        if not os.path.exists(basedir):
            os.makedirs(basedir)

        with open(path, 'a'):
            os.utime(path)

class CookiesManager(object):

    def __init__(self, browser_view):
        self.browser_view = browser_view

        self.cookies_dir = self.get_cookies_dir()

        # Both session and persistent cookies are stored in memory
        self.browser_view.page().profile().setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)

        self.cookie_store = self.browser_view.page().profile().cookieStore()

        self.cookie_store.cookieAdded.connect(self.add_cookie)      # save cookie to disk when captured cookieAdded signal
        self.cookie_store.cookieRemoved.connect(self.remove_cookie) # remove cookie stored on disk when captured cookieRemoved signal
        self.browser_view.loadStarted.connect(self.load_cookie)     # load disk cookie to QWebEngineView instance when page start load

    @classmethod
    def get_cookies_dir(cls):
        return os.path.join(get_emacs_config_dir(), "browser", "cookies")

    @classmethod
    def add_cookie(cls, cookie):
        '''Store cookie on disk.'''
        cookie_domain = cookie.domain()
        if not cookie.isSessionCookie():
            cookie_file = os.path.join(cls.get_cookies_dir(), cookie_domain, cls._generate_cookie_filename(cookie))
            touch(cookie_file)

            # Save newest cookie to disk.
            with open(cookie_file, "wb") as f:
                f.write(cookie.toRawForm())

    def load_cookie(self):
        ''' Load cookie file from disk.'''
        if not os.path.exists(self.cookies_dir):
            return

        all_cookies_domain = os.listdir(self.cookies_dir)
        for domain in filter(self.domain_matching, all_cookies_domain):
            from PyQt6.QtNetwork import QNetworkCookie

            domain_dir = os.path.join(self.cookies_dir, domain)

            for cookie_file in os.listdir(domain_dir):
                with open(os.path.join(domain_dir, cookie_file), "rb") as f:
                    for cookie in QNetworkCookie.parseCookies(f.read()):
                        if not domain.startswith('.'):
                            if self.browser_view.url().host() == domain:
                                # restore host-only cookie
                                cookie.setDomain('')
                                self.cookie_store.setCookie(cookie, self.browser_view.url())
                        else:
                            self.cookie_store.setCookie(cookie)

    def remove_cookie(self, cookie):
        ''' Delete cookie file.'''
        if not cookie.isSessionCookie():
            cookie_file = os.path.join(self.cookies_dir, cookie.domain(), self._generate_cookie_filename(cookie))

            if os.path.exists(cookie_file):
                os.remove(cookie_file)

    def delete_all_cookies(self):
        ''' Simply delete all cookies stored on memory and disk.'''
        self.cookie_store.deleteAllCookies()
        if os.path.exists(self.cookies_dir):
            import shutil
            shutil.rmtree(self.cookies_dir)

    def delete_cookie(self):
        ''' Delete all cookie used by current site except session cookies.'''
        from PyQt6.QtNetwork import QNetworkCookie
        import shutil

        cookies_domain = os.listdir(self.cookies_dir)

        for domain in filter(self.get_relate_domains, cookies_domain):
            domain_dir = os.path.join(self.cookies_dir, domain)

            for cookie_file in os.listdir(domain_dir):
                with open(os.path.join(domain_dir, cookie_file), "rb") as f:
                    for cookie in QNetworkCookie.parseCookies(f.read()):
                        self.cookie_store.deleteCookie(cookie)
            shutil.rmtree(domain_dir)

    def domain_matching(self, cookie_domain):
        ''' Check if a given cookie's domain is matching for host string.'''
        return True
        cookie_is_hostOnly = True
        if cookie_domain.startswith('.'):
            # get rid of prefixing dot when matching domains
            cookie_domain = cookie_domain[1:]
            cookie_is_hostOnly = False

        host_string = self.browser_view.url().host()
        if cookie_domain == host_string:
            # The domain string and the host string are identical
            return True

        if len(host_string) < len(cookie_domain):
            # For obvious reasons, the host string cannot be a suffix if the domain
            # is shorter than the domain string
            return False

        if host_string.endswith(cookie_domain) and host_string[:-len(cookie_domain)][-1] == '.' and not cookie_is_hostOnly:
            # The domain string should be a suffix of the host string,
            # The last character of the host string that is not included in the
            # domain string should be a %x2E (".") character.
            # and cookie domain not have prefixing dot (host-only cookie is not for subdomains)
            return True

        return False

    def get_relate_domains(self, cookie_domain):
        ''' Check whether the cookie domain is located under the same root host as the current URL host.'''
        import tld, re

        host_string = self.browser_view.url().host()

        if cookie_domain.startswith('.'):
            cookie_domain = cookie_domain[1:]

        base_domain = tld.get_fld(host_string, fix_protocol=True, fail_silently=True)

        if not base_domain:
            # check whether host string is an IP address
            if re.compile('^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$').match(host_string) and host_string == cookie_domain:
                return True
            return  False

        if cookie_domain == base_domain:
            return True

        if cookie_domain.endswith(base_domain) and cookie_domain[:-len(base_domain)][-1] == '.':
            return True

        return False

    @classmethod
    def _generate_cookie_filename(cls, cookie):
        ''' Gets the name of the cookie file stored on the hard disk.'''
        name = cookie.name().data().decode("utf-8")
        domain = cookie.domain()
        if os.name == "nt":
            encode_path = cookie.path().encode("utf-8").hex()
        else:
            encode_path = cookie.path().replace("/", "|")

        return name + "+" + domain + "+" + encode_path

class WebView(QWebEngineView):
    def __init__(self):
        super(QWebEngineView, self).__init__()
        self.cookies_manager = CookiesManager(self)

class BrowserPage(QWebEnginePage):
    def __init__(self):
        QWebEnginePage.__init__(self)

    def execute_javascript(self, script_src):
        ''' Execute JavaScript.'''
        # Build event loop.
        self.loop = QEventLoop()

        # Run JavaScript code.
        self.runJavaScript(script_src, self.callback_js)

        # Execute event loop, and wait event loop quit.
        self.loop.exec()

        # Return JavaScript function result.
        return self.result

    def callback_js(self, result):
        ''' Callback of JavaScript, call loop.quit to jump code after loop.exec.'''
        self.result = result
        self.loop.quit()

class WebWindow(QWidget):
    def __init__(self):
        super().__init__()
        global screen_size

        if platform.system() == "Windows":
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.ToolTip)
        self.setContentsMargins(0, 0, 0, 0)

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(0, 0, 0, 0)

        self.zoom_factor = get_emacs_var("popweb-zoom-factor")
        if screen_size.width() > 3000:
            self.zoom_factor = self.zoom_factor * 2

        self.loading_js_code = ""
        self.js_file_code = ""
        self.js_file_code_args = None
        self.load_finish_callback = None

        self.dark_mode_js = open(os.path.join(os.path.dirname(__file__), "darkreader.js")).read()

        self.update_theme_mode()
        self.webview = WebView()
        self.web_page = BrowserPage()
        self.webview.setPage(self.web_page)
        self.web_page.setBackgroundColor(QColor(get_emacs_func_result("popweb-get-theme-background", [])))

        self.developer_tools_view = QWebEngineView()
        self.developer_tools_view.setZoomFactor(self.zoom_factor)
        screen = QApplication.instance().primaryScreen()    # type: ignore
        self.developer_tools_view.resize(int(screen.size().width() / 2),
                                         int(screen.size().height() / 2))

        self.webview.loadStarted.connect(lambda : self.reset_zoom())
        self.webview.loadProgress.connect(lambda : self.execute_loading_js_code())
        self.webview.loadFinished.connect(self.execute_load_finish_js_code)
        self.reset_zoom()

        self.vbox.addWidget(self.webview)
        self.setLayout(self.vbox)

        self.webview.installEventFilter(self)

        self.settings = self.webview.settings()
        try:
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.FocusOnNavigationEnabled, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.AllowGeolocationOnInsecureOrigins, True)
            self.settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        except Exception:
            import traceback
            traceback.print_exc()

    def popup(self):
        self.show()

        enable_developer_tools = get_emacs_var("popweb-enable-developer-tools")
        if enable_developer_tools:
            self.web_page.setDevToolsPage(self.developer_tools_view.page())
            self.developer_tools_view.show()

    def reset_zoom(self):
        self.webview.setZoomFactor(self.zoom_factor)

    def update_theme_mode(self):
        self.theme_mode = get_emacs_func_result("popweb-get-theme-mode", [])

    def execute_loading_js_code(self):
        if self.loading_js_code != "":
            self.webview.page().runJavaScript(self.loading_js_code)

        if self.theme_mode == "dark":
            self.load_dark_mode_js()
            self.enable_dark_mode()

    def execute_load_finish_js_code(self):
        if self.js_file_code != "":
            js_code = self.js_file_code + "({0});".format(str(self.js_file_code_args or ""))
            self.webview.page().runJavaScript(js_code)
        if self.load_finish_callback is not None:
            self.load_finish_callback()

    def load_dark_mode_js(self):
        self.webview.page().runJavaScript('''if (typeof DarkReader === 'undefined') {{ {} }} '''.format(self.dark_mode_js))

    def enable_dark_mode(self):
        ''' Dark mode support.'''
        self.webview.page().runJavaScript("""DarkReader.setFetchMethod(window.fetch); DarkReader.enable({brightness: 100, contrast: 90, sepia: 10});""")

    def disable_dark_mode(self):
        ''' Remove dark mode support.'''
        self.webview.page().runJavaScript("""DarkReader.disable();""")

class POPWEB(object):
    def __init__(self, args):
        global proxy_string

        # Init EPC client port.
        init_epc_client(int(args[0]))

        # Build EPC server.
        self.server = ThreadingEPCServer(('127.0.0.1', 0), log_traceback=True)
        # self.server.logger.setLevel(logging.DEBUG)
        self.server.allow_reuse_address = True

        # ch = logging.FileHandler(filename=os.path.join(popweb_config_dir, 'epc_log.txt'), mode='w')
        # formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(lineno)04d | %(message)s')
        # ch.setFormatter(formatter)
        # ch.setLevel(logging.DEBUG)
        # self.server.logger.addHandler(ch)

        self.server.register_instance(self) # register instance functions let elisp side call

        # Start EPC server with sub-thread, avoid block Qt main loop.
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()

        self.web_window_dict = {}
        self.module_dict = {}

        self.get_emacs_func_result = get_emacs_func_result

        # Pass epc port and webengine codec information to Emacs when first start POPWEB.
        eval_in_emacs('popweb--first-start', [self.server.server_address[1]])

        # Disable use system proxy, avoid page slow when no network connected.
        QNetworkProxyFactory.setUseSystemConfiguration(False)

        # Set Network proxy.
        (proxy_host, proxy_port, proxy_type) = get_emacs_vars([
            "popweb-proxy-host",
            "popweb-proxy-port",
            "popweb-proxy-type"])

        self.proxy = (proxy_type, proxy_host, proxy_port)
        self.is_proxy = False

        if proxy_type != "" and proxy_host != "" and proxy_port != "":
            self.enable_proxy()

    def enable_proxy(self):
        global proxy_string

        proxy_string = "{0}://{1}:{2}".format(self.proxy[0], self.proxy[1], self.proxy[2])

        proxy = QNetworkProxy()
        if self.proxy[0] == "socks5":
            proxy.setType(QNetworkProxy.ProxyType.Socks5Proxy)
        elif self.proxy[0] == "http":
            proxy.setType(QNetworkProxy.ProxyType.HttpProxy)
        proxy.setHostName(self.proxy[1])
        proxy.setPort(int(self.proxy[2]))

        self.is_proxy = True
        QNetworkProxy.setApplicationProxy(proxy)

    def disable_proxy(self):
        global proxy_string

        proxy_string = ""

        proxy = QNetworkProxy()
        proxy.setType(QNetworkProxy.NoProxy)

        self.is_proxy = False
        QNetworkProxy.setApplicationProxy(proxy)

    def toggle_proxy(self):
        if self.is_proxy:
            self.disable_proxy()
        else:
            self.enable_proxy()

    def get_web_window(self, module_name):
        if module_name not in self.web_window_dict:
            self.web_window_dict[module_name] = WebWindow()

        return self.web_window_dict[module_name]

    def get_module(self, module_path):
        if module_path not in self.module_dict:
            spec = importlib.util.spec_from_file_location(module_path, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.module_dict[module_path] = module

        return self.module_dict[module_path]

    def adjust_render_pos(self, render_x, render_y, x_offset, y_offset, render_w, render_h, frame_x, frame_y, frame_w, frame_h):
        popup_pos = get_emacs_var("popweb-popup-pos")
        if popup_pos == "top-left":
            render_x = frame_x
            render_y = frame_y
        elif popup_pos == "top-right":
            render_x = frame_x + frame_w - render_w - x_offset
            render_y = frame_y
        elif popup_pos == "bottom-left":
            render_x = frame_x
            render_y = frame_y + frame_h - render_h - y_offset
        elif popup_pos == "bottom-right":
            render_x = frame_x + frame_w - render_w - x_offset
            render_y = frame_y + frame_h - render_h - y_offset
        elif popup_pos == "point-bottom":
            render_x = max(int(render_x - render_w/2), 0)
            render_y = max(render_y, 0)
            if render_x + render_w > frame_x + frame_w:
                render_x = frame_x + frame_w - render_w - x_offset
            if render_y + render_h > frame_y + frame_h:
                render_y = render_y - render_h - y_offset
        elif popup_pos == "point-bottom-right":
            render_x = max(render_x, 0)
            render_y = max(render_y, 0)
            if render_x + render_w > frame_x + frame_w:
                render_x = frame_x + frame_w - render_w - x_offset
            if render_y + render_h > frame_y + frame_h:
                render_y = render_y - render_h - y_offset
        else:
            raise Exception('Cannot recognize Emacs variable popweb-popup-pos!')
        render_x = max(render_x, 0)
        render_y = max(render_y, 0)
        return render_x, render_y

    @PostGui()
    def call_module_method(self, module_path, method_name, method_args):
        module = self.get_module(module_path)
        method_args.insert(0, self)
        getattr(module, method_name)(*method_args)

    @PostGui()
    def hide_web_window(self, module_name):
        if module_name in self.web_window_dict:
            web_window = self.web_window_dict[module_name]
            web_window.hide()
            web_window.webview.load(QUrl(""))

            if hasattr(web_window, "developer_tools_view"):
                web_window.developer_tools_view.hide()

    def import_browser_cookies(self, browser, domain_name):
        try:
            import browser_cookie3
            cookiejar = getattr(browser_cookie3, browser)(domain_name=domain_name)
            if cookiejar is None:
                print("Import cookie failed!")
                return

            for cookie in cookiejar:
                qt_cookie = QNetworkCookie(cookie.name.encode(), cookie.value.encode())
                qt_cookie.setDomain(cookie.domain)
                qt_cookie.setSecure(cookie.secure)
                qt_cookie.setPath(cookie.path)
                qt_cookie.setExpirationDate(QDateTime.fromSecsSinceEpoch(0 if cookie.expires is None else cookie.expires))
                CookiesManager.add_cookie(qt_cookie)
        except:
            import traceback
            traceback.print_exc()

    def cleanup(self):
        '''Do some cleanup before exit python process.'''
        close_epc_client()

if __name__ == "__main__":
    proxy_string = ""

    destroy_view_list = []

    hardware_acceleration_args = []
    if platform.system() != "Windows":
        hardware_acceleration_args += [
            "--ignore-gpu-blocklist",
            "--enable-gpu-rasterization",
            "--enable-native-gpu-memory-buffers"]

    app = QApplication(sys.argv + hardware_acceleration_args)
    screen = app.primaryScreen()
    screen_size = screen.size()

    popweb = POPWEB(sys.argv[1:])

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.exit(app.exec())
