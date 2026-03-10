PYTHONPATH := src

.PHONY: test validate

test:
	PYTHONPATH=$(PYTHONPATH) python3 -m unittest discover -s tests -p 'test_*.py'

validate:
	PYTHONPATH=$(PYTHONPATH) python3 -m openclaw_hostctl.cli --config config/host-config.example.json validate-config

