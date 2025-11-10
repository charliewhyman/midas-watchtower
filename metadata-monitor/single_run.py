#!/usr/bin/env python3
"""
Single run script for AI Safety Metadata Monitor
"""

import os
import sys
from monitor import AISafetyMonitor

def main():
    print("Running single monitoring cycle...")
    
    monitor = AISafetyMonitor()
    changes = monitor.run_monitoring_cycle()
    
    print(f"Completed. Changes detected: {len(changes)}")
    
    # Print changes if any
    if changes:
        print("\nChanges detected:")
        for change in changes:
            url = change.get('url', 'Unknown URL')
            change_types = list(change.get('changes', {}).keys())
            print(f"  - {url}: {', '.join(change_types)}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())