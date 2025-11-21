# Telegram Bot

## Overview
Простой Telegram бот на Python, который отвечает на базовые команды и сообщения пользователей.

## Recent Changes
- **2025-11-21**: Создан простой Telegram бот с базовыми командами

## Features
- Команда `/start` - приветственное сообщение
- Команда `/help` - список доступных команд
- Эхо-ответы на текстовые сообщения пользователей
- Базовая обработка ошибок и логирование

## Setup
1. Получите токен бота от [@BotFather](https://t.me/botfather) в Telegram
2. Добавьте токен в переменную окружения `TELEGRAM_BOT_TOKEN`
3. Запустите бота командой `python main.py`

## Project Architecture
- `main.py` - основной файл бота с обработчиками команд
- `requirements.txt` - зависимости проекта (python-telegram-bot)

## Dependencies
- Python 3.11
- python-telegram-bot 21.0.1
