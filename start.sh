#!/bin/bash
# Установка браузера для Playwright
python -m playwright install chromium
python -m playwright install-deps
# Запуск бота
python main.py