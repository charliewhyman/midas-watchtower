from monitoring_service import MonitoringService

service = MonitoringService(config_path="config.yaml")
stats = service.run_cycle()
print(f"Cycle {stats.cycle_id} completed: {stats.changes_detected} changes detected")