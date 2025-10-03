import asyncio
from aiogram import Bot, Dispatcher, types

# 🔑 сюда вставь свой токен, который тебе дал BotFather
TOKEN = "8016036296:AAH9amLMhzhZXx2J_MGdwHe6riHRjgk9Dz0"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message()
async def echo_handler(message: types.Message):
    await message.answer(f"Ты написал: {message.text}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())