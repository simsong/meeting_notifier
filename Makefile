VENV_DIR := venv
PYTHON := python3
PROJECT := meeting-notifier-412417
.PHONY: all install run clean

all: install

install:
	$(PYTHON) -m venv $(VENV_DIR)
	. $(VENV_DIR)/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

clean:
	rm -rf $(VENV_DIR) *.pyc __pycache__ token.json

list-topics:
	gcloud pubsub topics list --project=$(PROJECT) --format="table(name)"

list-subscriptions:
	gcloud pubsub subscriptions list --project=$(PROJECT) --filter="name:meet-events-" --format="table(name,topic)"
