PREFIX?=	/usr/local
SHAREDIR=	${PREFIX}/share/dbuild
BINDIR=		${PREFIX}/bin
MANDIR=		${PREFIX}/share/man/man1

PYTHON?=	python3
RUFF?=		ruff

.PHONY: install upgrade deinstall check lint test man release fmt docs/dbuild.1

fmt:
	@which ${RUFF} >/dev/null 2>&1 || { echo "Error: ${RUFF} not found"; exit 1; }
	@${RUFF} check --fix dbuild/ tests/
	@echo "Code formatted."

lint:
	@which ${RUFF} >/dev/null 2>&1 || { echo "Error: ${RUFF} not found"; exit 1; }
	@${RUFF} check dbuild/ tests/

test:
	@PYTHONPATH=. ${PYTHON} -m pytest

check:
	${BINDIR}/dbuild ci-test-env

docs/dbuild.1:
	@mkdir -p docs
	@PREFIX=${PREFIX} PYTHONPATH=. ${PYTHON} -m dbuild --generate-manpage > docs/dbuild.1
	@echo "Generated docs/dbuild.1"

man: docs/dbuild.1

release: lint test man
	@echo "--- Preparing Release v$$(PYTHONPATH=. ${PYTHON} -c 'import dbuild; print(dbuild.VERSION)') ---"
	@echo "--- Success! You can now commit and tag the release. ---"

install: docs/dbuild.1
	@${PYTHON} -c 'import yaml' 2>/dev/null || \
		{ echo "Error: PyYAML is not installed"; exit 1; }
	mkdir -p ${SHAREDIR}
	cp -R dbuild ${SHAREDIR}/
	cp pyproject.toml ${SHAREDIR}/
	mkdir -p ${BINDIR}
	@printf '#!/bin/sh\n: $${PREFIX:=${PREFIX}}\nexport PREFIX\nPYTHONPATH=${SHAREDIR} exec ${PYTHON} -m dbuild "$$@"\n' > ${BINDIR}/dbuild
	chmod +x ${BINDIR}/dbuild
	mkdir -p ${MANDIR}
	cp docs/dbuild.1 ${MANDIR}/
	@echo "Installed dbuild to ${BINDIR}/dbuild"
	@echo "Installed man page to ${MANDIR}/dbuild.1"

upgrade: docs/dbuild.1
	cp -R dbuild ${SHAREDIR}/
	cp pyproject.toml ${SHAREDIR}/
	cp docs/dbuild.1 ${MANDIR}/
	@echo "Updated dbuild in ${SHAREDIR}"

deinstall:
	rm -f ${BINDIR}/dbuild
	rm -f ${MANDIR}/dbuild.1
	rm -rf ${SHAREDIR}
	@echo "Removed dbuild"
