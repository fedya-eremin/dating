import asyncio
from bot.config import bot, dp
from bot.storage.redis import queue_manager
from bot.handlers.common_handlers import *
from bot.handlers.profile_handlers import *
from bot.handlers.matching_handlers import *
from bot.handlers.referral import *

async def main():
    await queue_manager.connect()
    try:
        await dp.start_polling(bot)
    finally:
        await queue_manager.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
