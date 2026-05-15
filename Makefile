.PHONY: test dev docker-up health tui

test:
	python -m pytest backend/tests tui/tests -q

tui:
	python -m tui

dev:
	uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000

docker-up:
	docker compose up --build

health:
	curl -s http://127.0.0.1:8000/health
