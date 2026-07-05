"""Allow `python -m ipracticom_sweeper.telegram_bot` to launch the bot."""
from ipracticom_sweeper.telegram_bot.bot import main

if __name__ == "__main__":
    raise SystemExit(main())
