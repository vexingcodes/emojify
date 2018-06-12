emojify.zip: emojify.py Pipfile.lock
	pylint emojify.py
	rm -rf build
	mkdir -p build
	cp emojify.py build
	cp -r $(shell pipenv --venv)/lib/python3.6/site-packages/* build
	rm -rf build/boto*
	cd build && zip -r ../emojify.zip .
	rm -rf build
