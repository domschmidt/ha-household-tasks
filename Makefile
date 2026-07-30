.PHONY: install lint format test check docker-check

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"
	pre-commit install

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest -n 4 --dist=worksteal

check:
	sh scripts/check.sh

docker-check:
	docker build --file Dockerfile.dev --tag ha-household-tasks-dev .
	docker run --rm --init --volume "$(CURDIR):/workspace" --workdir /workspace ha-household-tasks-dev
