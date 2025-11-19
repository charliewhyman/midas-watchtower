import logging
from pathlib import Path
from datetime import datetime

# Setup logging
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler()
    ]
)

from monitoring_service import MonitoringService

service = MonitoringService(config_path="config.yaml")
stats = service.run_cycle()
print(f"Cycle {stats.cycle_id} completed: {stats.changes_detected} changes detected")