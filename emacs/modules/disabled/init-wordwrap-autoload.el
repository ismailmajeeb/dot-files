;;; editor/word-wrap/autoload.el -*- lexical-binding: t; -*-

;;; Code:
(defvar +word-wrap--major-mode-is-visual 1)
(defvar +word-wrap--major-mode-is-text 1)
(defvar +word-wrap--enable-adaptive-wrap-mode 1)
(defvar +word-wrap--enable-visual-line-mode 1)
(defvar +word-wrap--enable-visual-fill-mode 1)
(defvar +word-wrap--disable-auto-fill-mode 1)
(defvar +word-wrap--major-mode-indent-var 1)

(defvar adaptive-wrap-extra-indent 1)

;;;###autoload
(defun vanilla-point-in-comment-p (&optional pt)
  "Return non-nil if point is in a comment.
PT defaults to the current position."
  (let ((pt (or pt (point))))
    (save-excursion
      (cond ((syntax-ppss pt) ; Check if we're in a string
             nil)
            ((looking-at "[;%]")
	     (looking-at "\\/\\/") ; Check for C/C++-style single-line comments
	     (looking-at "#") ; Check for Python, Ruby, etc., single-line comments
	     (looking-at "--")) ; Check for Lua, SQL, etc., single-line comments
            ((and (looking-at "/\\*")
                  (looking-at "\\*/" (point-max))) ; Check for block comments
             t)
            (t
             nil)))))

(defun vanilla-point-in-string-p (&optional pt)
  "Return non-nil if point is inside a string.
If optional argument PT is present test this instead of point."
  (let ((pt (or pt (point))))
    (save-excursion
      (syntax-ppss pt))))

(defun vanilla-point-in-string-or-comment-p (&optional pos)
  "Return non-nil if POS is in a string or comment."
  (or (vanilla-point-in-string-p pos)
      (vanilla-point-in-comment-p pos)))

(defun +word-wrap--calc-extra-indent (p)
  "Calculate extra word-wrap indentation at point."
  (if (not (or +word-wrap--major-mode-is-text
               (vanilla-point-in-string-or-comment-p p)))
      (pcase +word-wrap-extra-indent
        ('double
         (* 2 (symbol-value +word-wrap--major-mode-indent-var)))
        ('single
         (symbol-value +word-wrap--major-mode-indent-var))
        ((and (pred integerp) fixed)
         fixed)
        (_ 0))
    0))

;;;###autoload
(define-minor-mode +word-wrap-mode
  "Wrap long lines in the buffer with language-aware indentation.

This mode configures `adaptive-wrap', `visual-line-mode' and
`visual-fill-column-mode' to wrap long lines without modifying the buffer
content. This is useful when dealing with legacy code which contains
gratuitously long lines, or running emacs on your wrist-phone.

Wrapped lines will be indented to match the preceding line. In code buffers,
lines which are not inside a string or comment will have additional indentation
according to the configuration of `+word-wrap-extra-indent'.

Long lines will wrap at the window margin by default, or can optionally be
wrapped at `fill-column' by configuring `+word-wrap-fill-style'."
  :init-value nil
  (if +word-wrap-mode
      (progn
        (setq-local
         +word-wrap--major-mode-is-visual
         (memq major-mode +word-wrap-visual-modes)
         +word-wrap--major-mode-is-text
         (memq major-mode +word-wrap-text-modes)
         +word-wrap--enable-adaptive-wrap-mode
         (and (not (bound-and-true-p adaptive-wrap-prefix-mode))
              (not +word-wrap--major-mode-is-visual))
         +word-wrap--enable-visual-line-mode
         (not (bound-and-true-p visual-line-mode))
         +word-wrap--enable-visual-fill-mode
         (and (not (bound-and-true-p visual-fill-column-mode))
              (memq +word-wrap-fill-style '(auto soft)))
         +word-wrap--disable-auto-fill-mode
         (and (bound-and-true-p auto-fill-function)
              (eq +word-wrap-fill-style 'soft)))

        (unless +word-wrap--major-mode-is-visual
          (require 'dtrt-indent) ; for dtrt-indent--search-hook-mapping
          (setq-local +word-wrap--major-mode-indent-var
                      (let ((indent-var (caddr (dtrt-indent--search-hook-mapping major-mode))))
                        (if (listp indent-var)
                            (car indent-var)
                          indent-var)))

          (advice-add #'adaptive-wrap-fill-context-prefix :around #'+word-wrap--adjust-extra-indent-a))

        (when +word-wrap--enable-adaptive-wrap-mode
          (adaptive-wrap-prefix-mode +1))
        (when +word-wrap--enable-visual-line-mode
          (visual-line-mode +1))
        (when +word-wrap--enable-visual-fill-mode
          (visual-fill-column-mode +1))
        (when +word-wrap--disable-auto-fill-mode
          (auto-fill-mode -1)))

    ;; disable +word-wrap-mode
    (unless +word-wrap--major-mode-is-visual
      (advice-remove #'adaptive-wrap-fill-context-prefix #'+word-wrap--adjust-extra-indent-a))

    (when +word-wrap--enable-adaptive-wrap-mode
      (adaptive-wrap-prefix-mode -1))
    (when +word-wrap--enable-visual-line-mode
      (visual-line-mode -1))
    (when +word-wrap--enable-visual-fill-mode
      (visual-fill-column-mode -1))
    (when +word-wrap--disable-auto-fill-mode
      (auto-fill-mode +1))))

(defun +word-wrap--enable-global-mode ()
  "Enable `+word-wrap-mode' for `+word-wrap-global-mode'.

Wrapping will be automatically enabled in all modes except special modes, or
modes explicitly listed in `+word-wrap-disabled-modes'."
  (unless (or (eq (get major-mode 'mode-class) 'special)
              (memq major-mode +word-wrap-disabled-modes))
    (+word-wrap-mode +1)))

;;;###autoload
(define-globalized-minor-mode +global-word-wrap-mode
  +word-wrap-mode
  +word-wrap--enable-global-mode)
