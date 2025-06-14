import os
import subprocess
from datetime import datetime

class RailwayDeployer:
    def __init__(self):
        self.current_time = "2025-06-14 03:50:33"
        self.current_user = "harshMrDev"
        
    def deploy(self):
        """Deploy to Railway"""
        print(f"""
╔══════════════════════════════════════╗
║         Railway Deployment           ║
║──────────────────────────────────────║
║  Time: {self.current_time}    ║
║  User: {self.current_user}              ║
╚══════════════════════════════════════╝
        """)
        
        try:
            # Check Railway CLI
            self._check_railway_cli()
            
            # Deploy steps
            steps = [
                ("Initializing...", "railway init"),
                ("Setting variables...", self._set_variables),
                ("Deploying...", "railway up"),
                ("Getting domain...", "railway domain")
            ]
            
            # Execute steps
            for step_name, command in steps:
                print(f"\n🔄 {step_name}")
                if callable(command):
                    command()
                else:
                    subprocess.run(command, shell=True, check=True)
                    
            print("\n✅ Deployment successful!")
            
        except Exception as e:
            print(f"\n❌ Deployment failed: {str(e)}")
            
    def _check_railway_cli(self):
        """Check if Railway CLI is installed"""
        try:
            subprocess.run(["railway", "--version"], 
                         capture_output=True, 
                         check=True)
        except:
            print("Installing Railway CLI...")
            subprocess.run(["npm", "i", "-g", "@railway/cli"], 
                         check=True)
                         
    def _set_variables(self):
        """Set Railway environment variables"""
        variables = {
            "CURRENT_TIME": self.current_time,
            "CURRENT_USER": self.current_user,
            "BOT_TOKEN": os.getenv("BOT_TOKEN")
        }
        
        for key, value in variables.items():
            subprocess.run(
                f'railway variables set {key}="{value}"',
                shell=True,
                check=True
            )

if __name__ == "__main__":
    deployer = RailwayDeployer()
    deployer.deploy()