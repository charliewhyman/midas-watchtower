import logging
import os
import json
from pathlib import Path
from datetime import datetime

def setup_logging():
    """Setup logging configuration"""
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
    return logging.getLogger(__name__)

def detect_first_run():
    """
    Detect if this is the first run by checking for existing data.
    Priority:
    1. FIRST_RUN environment variable (from GitHub Actions)
    2. Existing datastore with watches
    3. Previous monitoring reports
    """
    logger = logging.getLogger(__name__)
    
    # Check environment variable first (set by GitHub Actions)
    first_run_env = os.getenv('FIRST_RUN', '').lower()
    if first_run_env in ['false', '0', 'no']:
        logger.info("FIRST_RUN environment variable set to false - continuing from previous run")
        return False
    elif first_run_env in ['true', '1', 'yes']:
        logger.info("FIRST_RUN environment variable set to true - first run detected")
        return True
    
    # Fallback: Check for existing datastore
    datastore_path = Path("data/datastore")
    if datastore_path.exists():
        datastore_files = list(datastore_path.glob("*.json"))
        if datastore_files:
            logger.info(f"Found {len(datastore_files)} datastore files - continuing from previous run")
            
            # Try to check if datastore has watches configured
            for datastore_file in datastore_files:
                try:
                    with open(datastore_file, 'r') as f:
                        data = json.load(f)
                        if 'watches' in data and data['watches']:
                            watch_count = len(data['watches'])
                            logger.info(f"Datastore contains {watch_count} watches - continuing from previous run")
                            return False
                except (json.JSONDecodeError, KeyError, Exception) as e:
                    logger.debug(f"Could not parse {datastore_file}: {e}")
                    continue
    
    # Check for previous reports
    reports_path = Path("data/reports")
    if reports_path.exists():
        report_files = list(reports_path.glob("cycle_*.json"))
        if report_files:
            logger.info(f"Found {len(report_files)} previous reports - continuing from previous run")
            return False
    
    # No existing data found
    logger.info("No existing datastore or reports found - first run detected")
    return True

def ensure_data_directories():
    """Ensure all required data directories exist and are writable"""
    directories = [
        "data",
        "data/datastore", 
        "data/reports",
        "logs"
    ]
    
    for dir_path in directories:
        path = Path(dir_path)
        path.mkdir(exist_ok=True)
        try:
            # Try to create a test file to check write permissions
            test_file = path / ".write_test"
            test_file.touch()
            test_file.unlink()
        except Exception as e:
            logger.warning(f"Directory {dir_path} may not be writable: {e}")

def main():
    """Main monitoring execution"""
    # Setup logging first
    global logger
    logger = setup_logging()
    
    logger.info("=== Starting AI Safety Monitor ===")
    logger.info(f"GitHub Actions: {os.getenv('GITHUB_ACTIONS', 'false')}")
    logger.info(f"Run ID: {os.getenv('GITHUB_RUN_ID', 'local')}")
    logger.info(f"Run Attempt: {os.getenv('GITHUB_RUN_ATTEMPT', '1')}")
    
    # Ensure directories exist
    ensure_data_directories()
    
    # Detect first run status
    first_run = detect_first_run()
    
    # Log first run status
    if first_run:
        logger.info("🆕 FIRST RUN: Initializing new monitoring system")
        logger.info("This run will set up watches but may not detect changes yet")
    else:
        logger.info("🔄 CONTINUING: Resuming from previous monitoring data")
        logger.info("This run will check for changes in existing watches")
    
    # Set first run status as environment variable for the monitoring service
    os.environ['MONITOR_FIRST_RUN'] = str(first_run).lower()
    
    try:
        # Import and run monitoring service
        from monitoring_service import MonitoringService
        
        logger.info("Initializing MonitoringService...")
        service = MonitoringService(config_path="config.yaml")
        
        logger.info("Starting monitoring cycle...")
        stats = service.run_cycle()
        
        # Log results
        logger.info(f"✅ Cycle {stats.cycle_id} completed successfully")
        logger.info(f"📊 Results: {stats.urls_checked} URLs checked, {stats.changes_detected} changes detected")
        logger.info(f"📝 Sheets logged: {stats.sheets_logged}, Sheets failed: {stats.sheets_failed}")
        logger.info(f"⏱️  Duration: {stats.duration_seconds:.2f} seconds")
        
        print(f"Cycle {stats.cycle_id} completed: {stats.urls_checked} URLs checked, {stats.changes_detected} changes detected")
        
        return 0
        
    except Exception as e:
        logger.error(f"❌ Monitoring cycle failed: {e}")
        logger.exception("Full traceback:")
        print(f"Error: {e}")
        return 1

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)