import os, re
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
    
    