# Lyftr AI Chatbot Makefile

DOCKER_COMPOSE ?= docker-compose

.PHONY: up down logs test build help

help:
	@echo "Available commands:"
	@echo "  make up     - Build and start full stack in Docker Compose"
	@echo "  make down   - Stop and remove Docker Compose containers"
	@echo "  make logs   - Tail live logs of all services"
	@echo "  make test   - Run backend automated test suite"
	@echo "  make build  - Build Docker images"

up:
	$(DOCKER_COMPOSE) up --build -d

down:
	$(DOCKER_COMPOSE) down

logs:
	$(DOCKER_COMPOSE) logs -f

test:
	python -m pytest backend/tests -v

build:
	$(DOCKER_COMPOSE) build
