PREFIX?=	/usr/local
SHAREDIR=	${PREFIX}/share/dbuild
BINDIR=		${PREFIX}/bin

PYTHON?=	python3

.PHONY: install upgrade deinstall check

install:
	@${PYTHON} -c 'import yaml' 2>/dev/null || \
		{ echo "Error: PyYAML is not installed. Run: pkg install py311-pyyaml"; exit 1; }
	mkdir -p ${SHAREDIR}
	cp -R dbuild ${SHAREDIR}/
	cp pyproject.toml ${SHAREDIR}/
	@printf '#!/bin/sh\nPYTHONPATH=${SHAREDIR} exec ${PYTHON} -m dbuild "$$@"\n' > ${BINDIR}/dbuild
	chmod +x ${BINDIR}/dbuild
	@echo "Installed dbuild to ${BINDIR}/dbuild"

upgrade:
	cp -R dbuild ${SHAREDIR}/
	cp pyproject.toml ${SHAREDIR}/
	@echo "Updated dbuild in ${SHAREDIR}"

deinstall:
	rm -f ${BINDIR}/dbuild
	rm -rf ${SHAREDIR}
	@echo "Removed dbuild"

check:
	${BINDIR}/dbuild ci-test-env
