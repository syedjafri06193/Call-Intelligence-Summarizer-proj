# Call Intelligence Summarizer -- v1
#
# Everything here runs offline. No API key, no network, no cost to running it.

PYTHON ?= python3
export PYTHONPATH := src:.

.PHONY: help install test demo gate framework agreement lint clean

help:
	@echo "make install    install pyyaml and pytest"
	@echo "make test       run the suite"
	@echo "make demo       run the sample call end to end"
	@echo "make gate       run the consent gate, and again with a participant dropped"
	@echo "make framework  print MEDDICC as loaded"
	@echo "make agreement  inter-rater agreement on the bundled labels"
	@echo "make lint       compile-check every module"

install:
	$(PYTHON) -m pip install --break-system-packages -r requirements.txt

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m cis.cli demo

gate:
	$(PYTHON) -m cis.cli gate
	-@$(PYTHON) -m cis.cli gate --drop p_marco

framework:
	$(PYTHON) -m cis.cli framework

agreement:
	$(PYTHON) -m cis.cli agreement eval/labeled/sample_labels.json

lint:
	$(PYTHON) -m compileall -q src eval tests > /dev/null && echo "ok"

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
