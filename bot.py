import asyncio
import os
from aiogram import Bot, Dispatcher, types
# 🔑 Теперь токен берётся из переменной окружения
TOKEN = os.getenv("TOKEN")
bot = Bot(token=TOKEN)
dp = Dispatcher()
@dp.message()
async def echo_handler(message: types.Message):
    await message.answer(f"Ты написал: {message.text}")
async def main():
    await dp.start_polling(bot)
if __name__ == "__main__":
    asyncio.run(main())