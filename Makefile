PYTHON ?= python3

.PHONY: demo server simulate benchmark test compile

demo:
	$(PYTHON) -B demo.py

server:
	$(PYTHON) -B -m dart.server --drop-first-critical-ack

simulate:
	$(PYTHON) -B -m dart.simulator --server 127.0.0.1:9999 --sensors 5 --duration 8

benchmark:
	$(PYTHON) -B benchmark.py --quick

test:
	$(PYTHON) -B -m unittest discover -s tests -v

compile:
	PYTHONPYCACHEPREFIX=/tmp/dart_pycache $(PYTHON) -m compileall -q dart demo.py benchmark.py
