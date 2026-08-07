import os
import json
import gateway

def main():
    report_data = gateway.build_report()
    
    os.makedirs("out", exist_ok=True)
    with open("out/report.json", "w") as f:
        json.dump(report_data, f, indent=2)
        
    print("Report written successfully to out/report.json")

if __name__ == "__main__":
    main()
