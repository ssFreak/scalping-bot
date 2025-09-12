import yaml
import sys
import traceback
from bot_manager import BotManager
from core.logger import Logger


def load_config(config_path="config/config.yaml", logger):
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        logger.log(f"❌ Failed to load configuration: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    logger = Logger()

    # Încarcă configurația
    config = load_config(logger = logger)

    # Pornește managerul botului
    bot_manager = BotManager(config)
    bot_manager.run()
