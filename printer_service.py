import json
import win32api
import win32print
import pywintypes
from pathlib import Path
import shutil
import time
import os
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ---------- CONFIG ----------
BASE_DIR = Path(r"C:\Users\rushi\OneDrive\Desktop\automation")
ORDERS_DIR = BASE_DIR / "orders"
UPLOADS_DIR = BASE_DIR / "uploads"
PRINTED_DIR = BASE_DIR / "printed"
PRINTER_NAME = "HP LaserJet 1020"

# Create directories if they don't exist
PRINTED_DIR.mkdir(exist_ok=True)
# ----------------------------

class OrderHandler(FileSystemEventHandler):
    """Watches for new JSON order files"""
    
    def on_created(self, event):
        if event.is_directory:
            return
        
        if event.src_path.endswith('.json'):
            print(f"\nNew order detected: {Path(event.src_path).name}")
            time.sleep(1)  # Wait for file to be fully written
            process_order(event.src_path)

def get_printer_handle(printer_name):
    """Get handle to specific printer"""
    try:
        printers = [printer[2] for printer in win32print.EnumPrinters(2)]
        
        # Try exact match first
        for p in printers:
            if p.lower() == printer_name.lower():
                return p
        
        # Try partial match
        for p in printers:
            if printer_name.lower() in p.lower():
                print(f"Using printer: {p}")
                return p
        
        print(f"Printer '{printer_name}' not found. Available printers:")
        for p in printers:
            print(f"   - {p}")
        return None
    except Exception as e:
        print(f"Error accessing printers: {e}")
        return None

def clear_print_queue(printer_name):
    """Clear all jobs from printer queue"""
    try:
        printer = get_printer_handle(printer_name)
        if not printer:
            return False
        
        handle = win32print.OpenPrinter(printer)
        jobs = win32print.EnumJobs(handle, 0, -1, 1)
        
        if jobs:
            print(f"   Clearing {len(jobs)} old job(s)...")
            for job in jobs:
                try:
                    win32print.SetJob(handle, job['JobId'], 0, None, win32print.JOB_CONTROL_DELETE)
                except:
                    pass
            time.sleep(1)
        
        win32print.ClosePrinter(handle)
        return True
        
    except Exception as e:
        print(f"Could not clear queue: {e}")
        return False

def check_printer_status(printer_name):
    """Check if printer is ready"""
    try:
        printer = get_printer_handle(printer_name)
        if not printer:
            return False, "Printer not found"
        
        handle = win32print.OpenPrinter(printer)
        printer_info = win32print.GetPrinter(handle, 2)
        status = printer_info['Status']
        
        win32print.ClosePrinter(handle)
        
        if status == 0:
            return True, "Ready"
        elif status & win32print.PRINTER_STATUS_PAPER_OUT:
            return False, "Out of paper"
        elif status & win32print.PRINTER_STATUS_OFFLINE:
            return False, "Offline"
        elif status & win32print.PRINTER_STATUS_ERROR:
            return False, "Error state"
        else:
            return True, f"Status: {status}"
            
    except Exception as e:
        return False, str(e)

def find_sumatra_pdf():
    """Find SumatraPDF installation"""
    common_paths = [
        r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
        r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
        Path.home() / "AppData" / "Local" / "SumatraPDF" / "SumatraPDF.exe",
        Path(os.environ.get('LOCALAPPDATA', '')) / "SumatraPDF" / "SumatraPDF.exe",
    ]
    
    for path in common_paths:
        if Path(path).exists():
            return str(path)
    
    # Try to find in PATH
    try:
        result = subprocess.run(['where', 'SumatraPDF'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().split('\n')[0]
    except:
        pass
    
    return None

def print_pdf_sumatra(file_path, printer_name):
    """Print PDF using SumatraPDF (most reliable method)"""
    try:
        sumatra_path = find_sumatra_pdf()
        
        if not sumatra_path:
            print(f"   SumatraPDF not found")
            return False
        
        printer = get_printer_handle(printer_name)
        if not printer:
            return False
        
        file_path_abs = str(Path(file_path).resolve())
        
        # SumatraPDF command: -print-to "printer" -silent file.pdf
        cmd = [sumatra_path, '-print-to', printer, '-silent', file_path_abs]
        
        print(f"   Using SumatraPDF...")
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        
        if result.returncode == 0:
            print(f"   Print job sent successfully (SumatraPDF)")
            time.sleep(2)
            return True
        else:
            print(f"   SumatraPDF returned code: {result.returncode}")
            return False
        
    except subprocess.TimeoutExpired:
        print(f"   SumatraPDF timeout (job may still print)")
        return True
    except Exception as e:
        print(f"   SumatraPDF error: {e}")
        return False

def print_pdf_adobe(file_path, printer_name):
    """Print PDF using Adobe Reader"""
    try:
        adobe_paths = [
            r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe",
            r"C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe",
            r"C:\Program Files\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe",
        ]
        
        adobe_path = None
        for path in adobe_paths:
            if Path(path).exists():
                adobe_path = path
                break
        
        if not adobe_path:
            print(f"   Adobe Reader not found")
            return False
        
        printer = get_printer_handle(printer_name)
        if not printer:
            return False
        
        file_path_abs = str(Path(file_path).resolve())
        
        # Adobe command: /t file.pdf printer
        cmd = [adobe_path, '/t', file_path_abs, printer]
        
        print(f"   Using Adobe Reader...")
        subprocess.Popen(cmd)
        
        time.sleep(5)  # Wait for print job to be sent
        print(f"   Print job sent (Adobe Reader)")
        return True
        
    except Exception as e:
        print(f"   Adobe error: {e}")
        return False

def print_pdf_with_shellexecute(file_path, printer_name):
    """Print PDF using Windows shell print verb"""
    try:
        printer = get_printer_handle(printer_name)
        if not printer:
            return False
        
        # Set default printer temporarily
        current_default = win32print.GetDefaultPrinter()
        win32print.SetDefaultPrinter(printer)
        
        file_path_abs = str(Path(file_path).resolve())
        
        print(f"   Using Windows shell print...")
        
        # Use ShellExecute to print
        win32api.ShellExecute(
            0,
            "print",
            file_path_abs,
            None,
            ".",
            0
        )
        
        time.sleep(5)  # Wait for print job
        
        # Restore default printer
        try:
            win32print.SetDefaultPrinter(current_default)
        except:
            pass
        
        print(f"   Print job sent (Shell)")
        return True
        
    except Exception as e:
        print(f"   Shell print error: {e}")
        # Restore default printer on error
        try:
            win32print.SetDefaultPrinter(current_default)
        except:
            pass
        return False

def print_pdf_direct(file_path, printer_name):
    """Print PDF using multiple methods in order of reliability"""
    
    # Method 1: SumatraPDF (BEST - most reliable and silent)
    if print_pdf_sumatra(file_path, printer_name):
        return True
    
    # Method 2: Adobe Reader (GOOD - if installed)
    if print_pdf_adobe(file_path, printer_name):
        return True
    
    # Method 3: Windows Shell Execute (FALLBACK - opens default PDF viewer)
    if print_pdf_with_shellexecute(file_path, printer_name):
        return True
    
    # All methods failed
    print(f"   ❌ Could not print PDF")
    print(f"   Please install SumatraPDF: https://www.sumatrapdfreader.org/")
    return False

def print_file_method2(file_path, printer_name):
    """Print using mspaint (for images)"""
    try:
        print(f"   Using mspaint...")
        
        printer = get_printer_handle(printer_name)
        if not printer:
            return False
        
        cmd = f'mspaint /pt "{file_path}" "{printer}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
        
        if result.returncode == 0 or result.returncode == 1:
            print(f"   Print job sent")
            return True
        else:
            print(f"   mspaint returned code: {result.returncode}")
            return False
        
    except subprocess.TimeoutExpired:
        print(f"   Timeout (job may still print)")
        return True
    except Exception as e:
        print(f"   mspaint error: {e}")
        return False

def print_text_file(file_path, printer_name):
    """Print text files using notepad"""
    try:
        print(f"   Using notepad...")
        
        printer = get_printer_handle(printer_name)
        if not printer:
            return False
        
        # Set as default printer for notepad
        current_default = win32print.GetDefaultPrinter()
        win32print.SetDefaultPrinter(printer)
        
        cmd = f'notepad /p "{file_path}"'
        subprocess.Popen(cmd, shell=True)
        
        time.sleep(3)
        
        # Restore default printer
        try:
            win32print.SetDefaultPrinter(current_default)
        except:
            pass
        
        print(f"   Print job sent")
        return True
        
    except Exception as e:
        print(f"   Notepad error: {e}")
        return False

def print_office_file(file_path, printer_name):
    """Print Microsoft Office files"""
    try:
        from win32com import client
        
        printer = get_printer_handle(printer_name)
        if not printer:
            return False
        
        file_ext = Path(file_path).suffix.lower()
        file_path = str(Path(file_path).resolve())
        
        print(f"   Opening Office app for {file_ext}...")
        
        if file_ext in ['.doc', '.docx']:
            word = client.Dispatch("Word.Application")
            word.Visible = False
            
            doc = word.Documents.Open(file_path)
            doc.PrintOut(Background=False, Append=False, Range=0, Copies=1, 
                        PrintToFile=False, Collate=True, ActivePrinterMacGX=printer)
            
            time.sleep(3)
            doc.Close(False)
            word.Quit()
            
            print(f"   Print job sent (Word)")
            return True
            
        elif file_ext in ['.xls', '.xlsx']:
            excel = client.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            
            workbook = excel.Workbooks.Open(file_path)
            workbook.ActiveSheet.PrintOut(Copies=1, Collate=True, ActivePrinter=printer)
            
            time.sleep(3)
            workbook.Close(False)
            excel.Quit()
            
            print(f"   Print job sent (Excel)")
            return True
            
        elif file_ext in ['.ppt', '.pptx']:
            powerpoint = client.Dispatch("PowerPoint.Application")
            
            presentation = powerpoint.Presentations.Open(file_path, WithWindow=False)
            presentation.PrintOptions.ActivePrinter = printer
            presentation.PrintOut(Copies=1, Collate=True)
            
            time.sleep(3)
            presentation.Close()
            powerpoint.Quit()
            
            print(f"   Print job sent (PowerPoint)")
            return True
        
        else:
            print(f"   Unsupported Office file: {file_ext}")
            return False
            
    except Exception as e:
        print(f"   Office error: {e}")
        print(f"   Make sure Microsoft Office is installed")
        return False

def print_file(file_path, printer_name, options):
    """Send file to printer"""
    try:
        copies = options.get("copies", 1)
        color = options.get("color", False)
        sides = options.get("sides", "single")
        
        color_text = "Color" if color else "B&W"
        print(f"   Options: {copies} copies | {sides} | {color_text}")
        
        is_ready, status_msg = check_printer_status(printer_name)
        if not is_ready:
            print(f"   Printer not ready: {status_msg}")
            return False
        
        print(f"   Printer status: {status_msg}")
        clear_print_queue(printer_name)
        
        file_ext = Path(file_path).suffix.lower()
        success = False
        
        if file_ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif']:
            print(f"   Image file detected")
            success = print_file_method2(file_path, printer_name)
            
        elif file_ext == '.pdf':
            print(f"   PDF file detected")
            success = print_pdf_direct(file_path, printer_name)
            
        elif file_ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']:
            print(f"   Office file detected")
            success = print_office_file(file_path, printer_name)
            
        elif file_ext in ['.txt', '.log', '.csv']:
            print(f"   Text file detected")
            success = print_text_file(file_path, printer_name)
            
        else:
            print(f"   Unsupported file type: {file_ext}")
            return False
        
        if success:
            print(f"   ✓ Success!")
            time.sleep(2)
            return True
        else:
            print(f"   ✗ Failed to print")
            return False
        
    except Exception as e:
        print(f"   Print error: {e}")
        return False

def process_order(order_file_path):
    """Process a single order"""
    try:
        with open(order_file_path, "r", encoding="utf-8") as f:
            order = json.load(f)
        
        print(f"\nProcessing order {order['order_id']} for user {order['user_id']}")
        print("=" * 60)
        
        is_ready, status_msg = check_printer_status(PRINTER_NAME)
        print(f"Printer status: {status_msg}")
        
        if not is_ready:
            print(f"Cannot process - printer not ready")
            return
        
        clear_print_queue(PRINTER_NAME)
        
        success_count = 0
        for file_info in order["files"]:
            filename = Path(file_info["local_path"]).name
            file_path = UPLOADS_DIR / filename
            
            if not file_path.exists():
                print(f"\nFile not found: {file_path}")
                continue
            
            print(f"\nFile: {file_path.name}")
            options = file_info.get("print_options", {})
            
            if print_file(file_path, PRINTER_NAME, options):
                success_count += 1
                print(f"   File printed!")
        
        order_dest = PRINTED_DIR / Path(order_file_path).name
        if order_dest.exists():
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            order_dest = PRINTED_DIR / f"ORD_{timestamp}.json"
        
        shutil.move(order_file_path, order_dest)
        print(f"\nOrder JSON moved: {order_dest.name}")
        
        print("\n" + "=" * 60)
        print(f"Order complete! {success_count} file(s) sent to printer")
        print(f"Files remain in: {UPLOADS_DIR}")
        print(f"Order moved to: {PRINTED_DIR}")
        
    except Exception as e:
        print(f"Error processing order: {e}")

def process_existing_orders():
    """Move existing orders"""
    json_files = list(ORDERS_DIR.glob("*.json"))
    
    if json_files:
        print(f"Found {len(json_files)} existing order(s)")
        for json_file in json_files:
            order_dest = PRINTED_DIR / json_file.name
            if order_dest.exists():
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                order_dest = PRINTED_DIR / f"OLD_{timestamp}_{json_file.name}"
            shutil.move(str(json_file), order_dest)
            print(f"   Moved: {json_file.name}")
        print("Ready for new orders")
    else:
        print("No existing orders")

def main():
    """Main service loop"""
    print("=" * 60)
    print("AUTOMATIC PRINT SERVICE")
    print("=" * 60)
    print(f"Watching: {ORDERS_DIR}")
    print(f"Uploads:  {UPLOADS_DIR}")
    print(f"Orders:   {PRINTED_DIR}")
    print(f"Printer:  {PRINTER_NAME}")
    print("=" * 60)
    print("\nSupported file types:")
    print("   Images: JPG, PNG, BMP, GIF, TIFF")
    print("   PDF: PDF files")
    print("   Office: DOCX, XLSX, PPTX, DOC, XLS, PPT")
    print("   Text: TXT, LOG, CSV")
    print("=" * 60)
    
    # Check for PDF printing tools
    sumatra = find_sumatra_pdf()
    if sumatra:
        print(f"\n✓ SumatraPDF found: {sumatra}")
    else:
        print(f"\n⚠ SumatraPDF not found - install from:")
        print(f"  https://www.sumatrapdfreader.org/download-free-pdf-viewer")
    
    print("=" * 60)
    
    printer = get_printer_handle(PRINTER_NAME)
    if not printer:
        print("\nWARNING: Printer not found!\n")
    else:
        is_ready, status_msg = check_printer_status(PRINTER_NAME)
        print(f"\nPrinter status: {status_msg}\n")
    
    process_existing_orders()
    
    event_handler = OrderHandler()
    observer = Observer()
    observer.schedule(event_handler, str(ORDERS_DIR), recursive=False)
    observer.start()
    
    print(f"\nWatching for new orders...")
    print("Press Ctrl+C to stop\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nStopping service...")
        observer.stop()
    
    observer.join()
    print("Service stopped")

if __name__ == "__main__":
    main()