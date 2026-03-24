import os, re, json
from pathlib import Path
from pprint import pprint

class RuntimeHelper:

    @staticmethod
    def is_approved(response):
        match = re.search(r"STATUS\s*:\s*(APPROVED|REJECTED)", response["content"].upper())
        return bool(match and match.group(1) == "APPROVED")
    
    @staticmethod
    def get_status(response):
        match = re.search(r"STATUS\s*:\s*(APPROVED|REJECTED)", response["content"].upper())
        return "APPROVED" if match and match.group(1) == "APPROVED" else "REJECTED"
    
    @staticmethod
    def get_plan():
        try:
            with open("output/plan.md", "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return None

    @staticmethod
    def get_tasks():        
        try:
            with open("output/tasks.json", "r") as f:
                return json.load(f)
        except Exception as e:
            print("ERROR getting tasks:", e)
            return []

    @staticmethod
    def get_completed_tasks():
        try:
            with open("output/completed_tasks.json", "r") as f:
                return json.load(f)
        except Exception as e:
            print("ERROR getting completed tasks:", e)
            return []