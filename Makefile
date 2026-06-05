PREFIX      ?= /usr/local
BINDIR       = $(PREFIX)/bin
INSTALL_NAME = trueformat
SRC          = trueformat/trueformat.py

.PHONY: install uninstall check

install: check
	@echo "Installing $(INSTALL_NAME) to $(BINDIR) …"
	@install -d "$(BINDIR)"
	@install -m 0755 "$(SRC)" "$(BINDIR)/$(INSTALL_NAME)"
	@echo "Done. Run:  trueformat --help"

uninstall:
	@echo "Removing $(BINDIR)/$(INSTALL_NAME) …"
	@rm -f "$(BINDIR)/$(INSTALL_NAME)"
	@echo "Removed."

check:
	@python3 --version > /dev/null 2>&1 || (echo "python3 is required"; exit 1)
	@test -f "$(SRC)" || (echo "Source file not found: $(SRC)"; exit 1)
