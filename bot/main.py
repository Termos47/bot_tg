import logging
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncio

# Конфигурация
API_TOKEN = '7914747184:AAGrMX61goPxd-K8UkE7uYapQ7Pg7-dDHX8'
WEB_APP_URL = 'https://termos47.github.io/sitee'

# Инициализация
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

def webapp_keyboard():
    """Клавиатура с кнопкой для запуска веб-приложения"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="🚀 Запустить приложение",
        web_app=WebAppInfo(url=WEB_APP_URL)
    ))
    return builder.as_markup()

@router.message(Command("start", "help"))
async def send_welcome(message: types.Message):
    """Главное меню с инструкцией"""
    text = (
        "🎬 Добро пожаловать в кинотеку!\n\n"
        "Используйте кнопку ниже для запуска приложения\n"
        "или команду /app для повторного доступа"
    )
    await message.answer(text, reply_markup=webapp_keyboard())

@router.message(Command("app"))
async def webapp_command(message: types.Message):
    """Альтернативный запуск через команду"""
    await message.answer(
        "🖥️ Запуск веб-приложения:",
        reply_markup=webapp_keyboard()
    )

@router.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    """Обработчик данных из веб-приложения"""
    data = message.web_app_data.data
    await message.answer(f"Получены данные из приложения: {data}")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())