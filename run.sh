#!/bin/sh
# Запуск Sphaera Commander из корня репозитория
cd "$(dirname "$0")"
exec .venv/bin/python -m sphaera_commander "$@"
