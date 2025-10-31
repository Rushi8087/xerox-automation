import os, io, uuid, json
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import requests
from pathlib import Path
from PyPDF2 import PdfReader
from PIL import Image
from dotenv import load_dotenv
from datetime import datetime
import threading
import time
import math

load_dotenv()
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
NGROK_URL = os.getenv("NGROK_URL")  # Add this to your .env file
UPLOAD_DIR = Path("uploads")
ORDERS_DIR = Path("orders")
UPLOAD_DIR.mkdir(exist_ok=True)
ORDERS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
sessions = {}

SUPPORTED_FORMATS = {
    'pdf': ['pdf'],
    'image': ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif'],
    'document': ['doc', 'docx', 'txt', 'rtf', 'odt'],
    'spreadsheet': ['xls', 'xlsx', 'csv'],
    'presentation': ['ppt', 'pptx']
}

PRICING = {
    'sheet_bw': 1.1,
    'sheet_color': 6.0
}

def send_whatsapp_text(to_phone, text):
    """Send WhatsApp message"""
    url = f"https://graph.facebook.com/v17.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product":"whatsapp", "to": to_phone, "type":"text", "text": {"body": text}}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Send error: {e}")
        return None

def download_media_fast(media_id, filename):
    """Download media file"""
    try:
        url = f"https://graph.facebook.com/v17.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        media_url = r.json().get("url")
        
        r2 = requests.get(media_url, headers=headers, stream=True, timeout=30)
        r2.raise_for_status()
        
        path = UPLOAD_DIR / filename
        with open(path, "wb") as f:
            for chunk in r2.iter_content(chunk_size=128*1024):
                if chunk:
                    f.write(chunk)
        return str(path), media_url
    except Exception as e:
        print(f"Download error: {e}")
        raise

def get_file_extension(filename):
    return filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''

def count_pages_smart(file_path, file_ext):
    """Count pages based on file type"""
    try:
        if file_ext == 'pdf':
            reader = PdfReader(file_path)
            return len(reader.pages)
        elif file_ext in SUPPORTED_FORMATS['image']:
            if file_ext in ['tiff', 'tif']:
                try:
                    img = Image.open(file_path)
                    pages = 1
                    try:
                        while True:
                            img.seek(img.tell() + 1)
                            pages += 1
                    except EOFError:
                        pass
                    return pages
                except:
                    return 1
            return 1
        elif file_ext in SUPPORTED_FORMATS['document']:
            file_size = os.path.getsize(file_path)
            return max(1, min(100, file_size // 3000 if file_ext == 'txt' else file_size // 50000))
        else:
            return 1
    except Exception as e:
        print(f"Page count error: {e}")
        return 1

def is_supported_format(filename):
    ext = get_file_extension(filename)
    all_formats = []
    for formats in SUPPORTED_FORMATS.values():
        all_formats.extend(formats)
    return ext in all_formats

def send_web_link(from_phone, session_id):
    """Send web interface link to user"""
    web_url = f"{NGROK_URL}/order/{session_id}"
    message = f"🔗 {web_url}"
    send_whatsapp_text(from_phone, message)

def process_uploaded_file(from_phone, media_id, filename):
    """Process uploaded file and add to session"""
    job = sessions.get(from_phone)
    if not job:
        return False
    
    try:
        print(f"📥 Downloading: {filename}")
        local_path, file_url = download_media_fast(media_id, filename)
        file_ext = get_file_extension(filename)
        pages = count_pages_smart(local_path, file_ext)
        
        file_id = f"FILE_{len(job['order_data']['files']) + 1}"
        file_obj = {
            "file_id": file_id,
            "file_url": file_url,
            "filename": filename,
            "file_type": file_ext,
            "local_path": local_path,
            "print_options": {
                "color": False,
                "sides": "double",
                "copies": 1
            },
            "page_count": pages,
            "sheets_required": None,
            "total_sheets": None,
            "price": None,
            "processing_status": "pending"
        }
        
        job["order_data"]["files"].append(file_obj)
        print(f"✅ Processed: {filename} ({pages} pages)")
        return True
        
    except Exception as e:
        print(f"❌ Failed to process {filename}: {e}")
        return False

@app.route("/")
def home():
    return "WhatsApp Print Shop Bot is running!"

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method == "GET":
        verify_token = os.getenv("WEBHOOK_VERIFY_TOKEN", "verifytoken123")
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == verify_token:
            return challenge, 200
        return "Verification failed", 403

    try:
        data = request.get_json(force=True)
    except Exception as e:
        return jsonify({"status":"error"}), 400

    entries = data.get("entry") or []
    messages = []
    for ent in entries:
        for change in ent.get("changes", []):
            value = change.get("value", {})
            msgs = value.get("messages") or []
            for m in msgs:
                messages.append((m, value))

    for m, value in messages:
        from_phone = m.get("from")
        msg_type = m.get("type")
        
        # Initialize session
        if from_phone not in sessions:
            session_id = uuid.uuid4().hex[:12].upper()
            sessions[from_phone] = {
                "session_id": session_id,
                "order_placed": False,
                "order_data": {
                    "order_id": f"ORD_{uuid.uuid4().hex[:8].upper()}",
                    "session_id": session_id,
                    "user_id": from_phone,
                    "timestamp": datetime.utcnow().isoformat(),
                    "files": [],
                    "total_price": None,
                    "total_pages": None,
                    "total_sheets": None,
                    "payment_status": "pending",
                    "order_status": "pending"
                }
            }
        
        job = sessions[from_phone]
        session_id = job["session_id"]

        # Handle TEXT messages
        if msg_type == "text":
            text = m.get("text", {}).get("body", "").strip().lower()
            
            # RESTART KEYWORD: "hi" resets everything
            if text == "hi":
                session_id = uuid.uuid4().hex[:12].upper()
                sessions[from_phone] = {
                    "session_id": session_id,
                    "order_placed": False,
                    "order_data": {
                        "order_id": f"ORD_{uuid.uuid4().hex[:8].upper()}",
                        "session_id": session_id,
                        "user_id": from_phone,
                        "timestamp": datetime.utcnow().isoformat(),
                        "files": [],
                        "total_price": None,
                        "total_pages": None,
                        "total_sheets": None,
                        "payment_status": "pending",
                        "order_status": "pending"
                    }
                }
                
                greeting = (
                    "👋 *Welcome to Print Shop!*\n\n"
                    "💰 Pricing:\n"
                    "• B&W: ₹1.1/sheet\n"
                    "• Color: ₹6/sheet\n\n"
                    "📤 Send your files to get started!"
                )
                send_whatsapp_text(from_phone, greeting)
                time.sleep(0.5)
                send_web_link(from_phone, session_id)
            else:
                send_web_link(from_phone, session_id)

        # Handle IMAGE and DOCUMENT uploads
        elif msg_type in ("image", "document"):
            media_obj = m.get(msg_type) or {}
            media_id = media_obj.get("id")
            
            # Generate filename
            if msg_type == "image":
                mime_type = media_obj.get("mime_type", "image/jpeg")
                ext = mime_type.split('/')[-1].replace('jpeg', 'jpg')
                filename = media_obj.get("filename") or f"img_{uuid.uuid4().hex[:8]}.{ext}"
            else:
                filename = media_obj.get("filename") or f"doc_{uuid.uuid4().hex[:8]}.pdf"
            
            if not is_supported_format(filename):
                send_whatsapp_text(from_phone, f"❌ {filename}: Unsupported format")
                continue
            
            # Process file
            success = process_uploaded_file(from_phone, media_id, filename)
            
            if success:
                send_whatsapp_text(from_phone, f"✓ {filename} uploaded!")
                time.sleep(0.5)
                send_web_link(from_phone, session_id)

    return jsonify({"status":"received"}), 200

@app.route("/order/<session_id>")
def order_page(session_id):
    """Web interface for configuring print order"""
    
    HTML_TEMPLATE = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Print Shop - Configure Order</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            
            .container {
                max-width: 900px;
                margin: 0 auto;
            }
            
            .header {
                text-align: center;
                color: white;
                margin-bottom: 30px;
            }
            
            .header h1 {
                font-size: 2.5rem;
                margin-bottom: 10px;
            }
            
            .pricing-info {
                background: rgba(255, 255, 255, 0.2);
                backdrop-filter: blur(10px);
                border-radius: 15px;
                padding: 20px;
                color: white;
                margin-bottom: 30px;
            }
            
            .upload-area {
                background: white;
                border-radius: 15px;
                padding: 40px;
                text-align: center;
                border: 3px dashed #667eea;
                cursor: pointer;
                transition: all 0.3s;
                margin-bottom: 30px;
            }
            
            .upload-area:hover {
                border-color: #764ba2;
                background: #f8f9ff;
            }
            
            .upload-area.dragover {
                background: #e8ebff;
                border-color: #764ba2;
            }
            
            .files-container {
                display: flex;
                flex-direction: column;
                gap: 15px;
                margin-bottom: 30px;
            }
            
            .file-card {
                background: white;
                border-radius: 15px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            }
            
            .file-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 15px;
            }
            
            .file-name {
                font-weight: bold;
                font-size: 1.1rem;
                color: #333;
                flex: 1;
            }
            
            .remove-btn {
                background: #ff4757;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 8px;
                cursor: pointer;
                font-weight: bold;
            }
            
            .remove-btn:hover {
                background: #ff3838;
            }
            
            .file-options {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 15px;
                margin-bottom: 15px;
            }
            
            .option-group {
                display: flex;
                flex-direction: column;
                gap: 5px;
            }
            
            .option-group label {
                font-weight: 600;
                color: #666;
                font-size: 0.9rem;
            }
            
            .option-group select,
            .option-group input {
                padding: 10px;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                font-size: 1rem;
                transition: border 0.3s;
            }
            
            .option-group select:focus,
            .option-group input:focus {
                outline: none;
                border-color: #667eea;
            }
            
            .file-price {
                text-align: right;
                font-size: 1.3rem;
                font-weight: bold;
                color: #667eea;
            }
            
            .summary {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 15px;
                padding: 30px;
                color: white;
                margin-bottom: 20px;
            }
            
            .summary h2 {
                margin-bottom: 20px;
            }
            
            .summary-row {
                display: flex;
                justify-content: space-between;
                margin-bottom: 10px;
                font-size: 1.1rem;
            }
            
            .summary-total {
                font-size: 1.8rem;
                font-weight: bold;
                border-top: 2px solid rgba(255, 255, 255, 0.3);
                padding-top: 15px;
                margin-top: 15px;
            }
            
            .action-buttons {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 15px;
            }
            
            .btn {
                padding: 18px;
                border: none;
                border-radius: 12px;
                font-size: 1.1rem;
                font-weight: bold;
                cursor: pointer;
                transition: all 0.3s;
            }
            
            .btn-primary {
                background: #10ac84;
                color: white;
            }
            
            .btn-primary:hover:not(:disabled) {
                background: #0e9770;
                transform: translateY(-2px);
                box-shadow: 0 8px 15px rgba(16, 172, 132, 0.3);
            }
            
            .btn-primary:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            
            .btn-secondary {
                background: #ff6b6b;
                color: white;
            }
            
            .btn-secondary:hover {
                background: #ee5a52;
            }
            
            .hidden {
                display: none;
            }
            
            .loading {
                text-align: center;
                padding: 20px;
                color: white;
                font-size: 1.2rem;
            }
            
            @media (max-width: 768px) {
                .file-options {
                    grid-template-columns: 1fr;
                }
                
                .action-buttons {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📄 Print Shop</h1>
                <p>Upload your documents and configure print settings</p>
            </div>
            
            <div class="pricing-info">
                <h3>💰 Pricing (per sheet):</h3>
                <p>B&W: ₹1.1/sheet | Color: ₹6/sheet</p>
                <p style="margin-top: 10px; font-size: 0.9rem;">
                    Single-sided: 1 page = 1 sheet | Double-sided: 2 pages = 1 sheet
                </p>
            </div>
            
            <div class="upload-area" id="uploadArea">
                <div style="font-size: 3rem; margin-bottom: 15px;">📤</div>
                <h3>Drop files here or click to upload</h3>
                <p style="color: #666; margin-top: 10px;">Supports: PDF, Images, DOC, XLS, PPT</p>
                <input type="file" id="fileInput" multiple accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx,.ppt,.pptx" class="hidden">
            </div>
            
            <div id="loadingIndicator" class="loading hidden">
                <div style="font-size: 2rem; margin-bottom: 10px;">⏳</div>
                <div>Uploading files...</div>
            </div>
            
            <div class="files-container" id="filesContainer"></div>
            
            <div class="summary" id="summary" style="display: none;">
                <h2>📋 Order Summary</h2>
                <div class="summary-row">
                    <span>Total Pages:</span>
                    <span id="totalPages">0</span>
                </div>
                <div class="summary-row">
                    <span>Total Sheets:</span>
                    <span id="totalSheets">0</span>
                </div>
                <div class="summary-row summary-total">
                    <span>Total Price:</span>
                    <span id="totalPrice">₹0.00</span>
                </div>
            </div>
            
            <div class="action-buttons">
                <button class="btn btn-primary" id="placeOrderBtn" onclick="placeOrder()">
                    💳 Place Order
                </button>
                <button class="btn btn-secondary" onclick="clearAll()">
                    🗑️ Clear All
                </button>
            </div>
        </div>
        
        <script>
            const SESSION_ID = "{{ session_id }}";
            let files = [];
            
            // Load existing files
            async function loadFiles() {
                try {
                    const response = await fetch(`/api/order/${SESSION_ID}`);
                    const data = await response.json();
                    
                    if (data.files && data.files.length > 0) {
                        files = data.files;
                        renderFiles();
                    }
                } catch (error) {
                    console.error('Error loading files:', error);
                }
            }
            
            // Upload area interactions
            const uploadArea = document.getElementById('uploadArea');
            const fileInput = document.getElementById('fileInput');
            const loadingIndicator = document.getElementById('loadingIndicator');
            
            uploadArea.addEventListener('click', () => fileInput.click());
            
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.classList.add('dragover');
            });
            
            uploadArea.addEventListener('dragleave', () => {
                uploadArea.classList.remove('dragover');
            });
            
            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.classList.remove('dragover');
                handleFiles(e.dataTransfer.files);
            });
            
            fileInput.addEventListener('change', (e) => {
                handleFiles(e.target.files);
                fileInput.value = ''; // Reset input
            });
            
            async function handleFiles(fileList) {
                if (!fileList || fileList.length === 0) {
                    return;
                }
                
                const formData = new FormData();
                
                for (let file of fileList) {
                    formData.append('files', file);
                }
                
                formData.append('session_id', SESSION_ID);
                
                // Show loading
                loadingIndicator.classList.remove('hidden');
                uploadArea.style.opacity = '0.5';
                uploadArea.style.pointerEvents = 'none';
                
                try {
                    const response = await fetch('/api/upload', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        files = data.files;
                        renderFiles();
                        
                        // Show success message
                        alert(`Successfully uploaded ${fileList.length} file(s)`);
                    } else {
                        alert('Failed to upload files: ' + (data.error || 'Unknown error'));
                    }
                } catch (error) {
                    console.error('Upload error:', error);
                    alert('Failed to upload files. Please try again.');
                } finally {
                    // Hide loading
                    loadingIndicator.classList.add('hidden');
                    uploadArea.style.opacity = '1';
                    uploadArea.style.pointerEvents = 'auto';
                }
            }
            
            function renderFiles() {
                const container = document.getElementById('filesContainer');
                const summary = document.getElementById('summary');
                
                if (files.length === 0) {
                    container.innerHTML = '';
                    summary.style.display = 'none';
                    return;
                }
                
                summary.style.display = 'block';
                
                container.innerHTML = files.map((file, index) => `
                    <div class="file-card">
                        <div class="file-header">
                            <div class="file-name">📄 ${file.filename} <span style="color: #999; font-size: 0.9rem;">(${file.page_count} pages)</span></div>
                            <button class="remove-btn" onclick="removeFile(${index})">✕</button>
                        </div>
                        <div class="file-options">
                            <div class="option-group">
                                <label>Print Mode</label>
                                <select onchange="updateFile(${index}, 'sides', this.value)">
                                    <option value="double" ${file.print_options.sides === 'double' ? 'selected' : ''}>Double-sided</option>
                                    <option value="single" ${file.print_options.sides === 'single' ? 'selected' : ''}>Single-sided</option>
                                </select>
                            </div>
                            <div class="option-group">
                                <label>Color</label>
                                <select onchange="updateFile(${index}, 'color', this.value === 'true')">
                                    <option value="false" ${!file.print_options.color ? 'selected' : ''}>B&W</option>
                                    <option value="true" ${file.print_options.color ? 'selected' : ''}>Color</option>
                                </select>
                            </div>
                            <div class="option-group">
                                <label>Copies</label>
                                <input type="number" min="1" max="100" value="${file.print_options.copies}" onchange="updateFile(${index}, 'copies', parseInt(this.value))">
                            </div>
                        </div>
                        <div class="file-price" id="price_${index}">₹${calculatePrice(file).toFixed(2)}</div>
                    </div>
                `).join('');
                
                updateSummary();
            }
            
            function calculatePrice(file) {
                const pages = file.page_count;
                const copies = file.print_options.copies;
                const color = file.print_options.color;
                const sides = file.print_options.sides;
                
                let sheets = sides === 'single' ? pages : Math.ceil(pages / 2);
                let totalSheets = sheets * copies;
                let rate = color ? 6.0 : 1.1;
                
                return totalSheets * rate;
            }
            
            async function updateFile(index, key, value) {
                files[index].print_options[key] = value;
                
                // Update on server
                try {
                    await fetch('/api/update', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            session_id: SESSION_ID,
                            files: files
                        })
                    });
                    
                    renderFiles();
                } catch (error) {
                    console.error('Update error:', error);
                }
            }
            
            async function removeFile(index) {
                if (!confirm('Remove this file?')) {
                    return;
                }
                
                files.splice(index, 1);
                
                try {
                    await fetch('/api/update', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            session_id: SESSION_ID,
                            files: files
                        })
                    });
                    
                    renderFiles();
                } catch (error) {
                    console.error('Remove error:', error);
                }
            }
            
            function updateSummary() {
                let totalPages = 0;
                let totalSheets = 0;
                let totalPrice = 0;
                
                files.forEach(file => {
                    totalPages += file.page_count;
                    
                    const sheets = file.print_options.sides === 'single' 
                        ? file.page_count 
                        : Math.ceil(file.page_count / 2);
                    
                    totalSheets += sheets * file.print_options.copies;
                    totalPrice += calculatePrice(file);
                });
                
                document.getElementById('totalPages').textContent = totalPages;
                document.getElementById('totalSheets').textContent = totalSheets;
                document.getElementById('totalPrice').textContent = `₹${totalPrice.toFixed(2)}`;
            }
            
            async function placeOrder() {
                const btn = document.getElementById('placeOrderBtn');
                
                if (files.length === 0) {
                    alert('Please upload at least one file');
                    return;
                }
                
                // Disable button to prevent double-click
                if (btn.disabled) {
                    return;
                }
                
                btn.disabled = true;
                btn.style.opacity = '0.5';
                btn.style.cursor = 'not-allowed';
                btn.innerHTML = '⏳ Processing...';
                
                try {
                    const response = await fetch('/api/place-order', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            session_id: SESSION_ID
                        })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        btn.innerHTML = '✅ Order Placed!';
                        alert('Order placed successfully! Redirecting to payment...');
                        
                        // Redirect to UPI payment
                        setTimeout(() => {
                            window.location.href = data.payment_url;
                        }, 1500);
                    } else {
                        alert(data.message || data.error || 'Failed to place order');
                        // Re-enable button if there was an error
                        btn.disabled = false;
                        btn.style.opacity = '1';
                        btn.style.cursor = 'pointer';
                        btn.innerHTML = '💳 Place Order';
                    }
                } catch (error) {
                    console.error('Order error:', error);
                    alert('Failed to place order. Please try again.');
                    
                    // Re-enable button on error
                    btn.disabled = false;
                    btn.style.opacity = '1';
                    btn.style.cursor = 'pointer';
                    btn.innerHTML = '💳 Place Order';
                }
            }
            
            function clearAll() {
                if (confirm('Clear all files?')) {
                    files = [];
                    renderFiles();
                    
                    fetch('/api/update', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            session_id: SESSION_ID,
                            files: []
                        })
                    });
                }
            }
            
            // Load files on page load
            loadFiles();
        </script>
    </body>
    </html>
    '''
    
    return render_template_string(HTML_TEMPLATE, session_id=session_id)

@app.route("/api/order/<session_id>")
def get_order_api(session_id):
    """Get order data by session ID"""
    for phone, job in sessions.items():
        if job.get("session_id") == session_id:
            return jsonify(job["order_data"])
    return jsonify({"files": []})

@app.route("/api/upload", methods=["POST"])
def upload_files():
    """Handle file uploads from web interface"""
    try:
        print("📤 Upload request received")
        print(f"Form data: {request.form}")
        print(f"Files: {request.files}")
        
        session_id = request.form.get('session_id')
        print(f"Session ID: {session_id}")
        
        if not session_id:
            return jsonify({"success": False, "error": "Session ID required"})
        
        # Get files from request
        uploaded_files = request.files.getlist('files')
        print(f"Number of files received: {len(uploaded_files)}")
        
        if not uploaded_files or len(uploaded_files) == 0:
            return jsonify({"success": False, "error": "No files uploaded"})
        
        # Find session
        job = None
        for phone, sess in sessions.items():
            if sess.get("session_id") == session_id:
                job = sess
                break
        
        if not job:
            print(f"❌ Session not found: {session_id}")
            return jsonify({"success": False, "error": "Session not found"})
        
        print(f"✅ Session found for phone: {job['order_data']['user_id']}")
        
        uploaded_count = 0
        errors = []
        
        for file in uploaded_files:
            if not file or not file.filename:
                print("⚠️ Empty file or no filename")
                continue
                
            filename = file.filename
            print(f"Processing file: {filename}")
            
            # Check if supported format
            if not is_supported_format(filename):
                error_msg = f"Unsupported format: {filename}"
                print(f"⚠️ {error_msg}")
                errors.append(error_msg)
                continue
            
            try:
                # Generate unique filename to avoid conflicts
                unique_filename = f"{uuid.uuid4().hex[:8]}_{filename}"
                file_path = UPLOAD_DIR / unique_filename
                
                # Save file
                file.save(str(file_path))
                print(f"✅ Saved to: {file_path}")
                
                # Verify file was saved
                if not file_path.exists():
                    raise Exception("File not saved to disk")
                
                file_size = os.path.getsize(file_path)
                print(f"File size: {file_size} bytes")
                
                # Count pages
                file_ext = get_file_extension(filename)
                pages = count_pages_smart(str(file_path), file_ext)
                print(f"Page count: {pages}")
                
                # Add to order
                file_id = f"FILE_{len(job['order_data']['files']) + 1}"
                file_obj = {
                    "file_id": file_id,
                    "filename": filename,
                    "file_type": file_ext,
                    "local_path": str(file_path),
                    "print_options": {
                        "color": False,
                        "sides": "double",
                        "copies": 1
                    },
                    "page_count": pages,
                    "processing_status": "pending"
                }
                
                job["order_data"]["files"].append(file_obj)
                uploaded_count += 1
                print(f"✅ Added to order: {filename} ({pages} pages)")
                
            except Exception as e:
                error_msg = f"Error processing {filename}: {str(e)}"
                print(f"❌ {error_msg}")
                errors.append(error_msg)
                import traceback
                traceback.print_exc()
                continue
        
        if uploaded_count == 0:
            error_detail = " | ".join(errors) if errors else "Unknown error"
            return jsonify({
                "success": False, 
                "error": f"No files were successfully uploaded. {error_detail}"
            })
        
        print(f"✅ Successfully uploaded {uploaded_count} file(s)")
        return jsonify({
            "success": True, 
            "files": job["order_data"]["files"],
            "uploaded_count": uploaded_count,
            "errors": errors if errors else None
        })
        
    except Exception as e:
        print(f"❌ Upload error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/update", methods=["POST"])
def update_order():
    """Update order data"""
    try:
        data = request.json
        session_id = data.get('session_id')
        files = data.get('files')
        
        if not session_id:
            return jsonify({"success": False, "error": "Session ID required"})
        
        for phone, job in sessions.items():
            if job.get("session_id") == session_id:
                job["order_data"]["files"] = files
                return jsonify({"success": True})
        
        return jsonify({"success": False, "error": "Session not found"})
        
    except Exception as e:
        print(f"❌ Update error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/place-order", methods=["POST"])
def place_order():
    """Finalize order and generate payment link"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        # Find session
        job = None
        phone = None
        for p, sess in sessions.items():
            if sess.get("session_id") == session_id:
                job = sess
                phone = p
                break
        
        if not job:
            return jsonify({"success": False, "error": "Session not found"})
        
        # Check if order already placed (prevent duplicate orders)
        if job.get("order_placed", False):
            return jsonify({
                "success": False, 
                "error": "Order already placed",
                "message": "This order has already been confirmed"
            })
        
        # Validate files exist
        if not job["order_data"]["files"] or len(job["order_data"]["files"]) == 0:
            return jsonify({
                "success": False,
                "error": "No files in order"
            })
        
        # Mark order as placed immediately to prevent duplicates
        job["order_placed"] = True
        
        # Calculate totals
        total_price = 0
        total_pages = 0
        total_sheets = 0
        
        for file_obj in job["order_data"]["files"]:
            pages = file_obj["page_count"]
            copies = file_obj["print_options"]["copies"]
            color = file_obj["print_options"]["color"]
            sides = file_obj["print_options"]["sides"]
            
            # Calculate sheets
            sheets = pages if sides == 'single' else math.ceil(pages / 2)
            total_sheets_file = sheets * copies
            
            # Calculate price
            rate = PRICING['sheet_color'] if color else PRICING['sheet_bw']
            price = total_sheets_file * rate
            
            file_obj["sheets_required"] = sheets
            file_obj["total_sheets"] = total_sheets_file
            file_obj["price"] = round(price, 2)
            file_obj["processing_status"] = "completed"
            
            total_price += price
            total_pages += pages
            total_sheets += total_sheets_file
        
        job["order_data"]["total_price"] = round(total_price, 2)
        job["order_data"]["total_pages"] = total_pages
        job["order_data"]["total_sheets"] = total_sheets
        job["order_data"]["order_status"] = "confirmed"
        job["order_data"]["order_placed_at"] = datetime.utcnow().isoformat()
        
        # Save order to JSON file
        order_id = job["order_data"]["order_id"]
        
        # Save to server orders directory
        server_path = ORDERS_DIR / f"{order_id}.json"
        with open(server_path, 'w') as f:
            json.dump(job["order_data"], f, indent=2)
        print(f"✅ Order saved to server: {server_path}")
        
        # Save to Downloads folder
        try:
            home = Path.home()
            downloads_dir = home / "Downloads"
            if not downloads_dir.exists():
                downloads_dir = home / "download"
            
            if downloads_dir.exists():
                pc_path = downloads_dir / f"{order_id}.json"
                with open(pc_path, 'w') as f:
                    json.dump(job["order_data"], f, indent=2)
                print(f"✅ Order saved to PC Downloads: {pc_path}")
            else:
                pc_path = Path(f"{order_id}.json")
                with open(pc_path, 'w') as f:
                    json.dump(job["order_data"], f, indent=2)
                print(f"✅ Order saved to current dir: {pc_path}")
        except Exception as e:
            print(f"⚠️ Could not save to Downloads: {e}")
        
        # Generate UPI payment link
        upi_id = "abhijeetkuntewad2-1@oksbi"
        payment_url = f"upi://pay?pa={upi_id}&pn=PrintShop&am={total_price}&cu=INR&tn=Order_{order_id}"
        
        # Send detailed confirmation to WhatsApp
        summary = f"✅ *Order #{order_id}*\n\n"
        
        for i, f in enumerate(job["order_data"]["files"], 1):
            opts = f['print_options']
            
            if opts["color"]:
                sides = "S"
                color = "C"
            else:
                sides = "S" if opts["sides"] == "single" else "D"
                color = "BW"
            
            sheets_info = f"{f['total_sheets']}sh" if 'total_sheets' in f else ""
            
            summary += f"{i}. {f['filename']}\n"
            summary += f"   {f['page_count']}p|{sides}|{color}|{opts['copies']}x = {sheets_info} = ₹{f['price']}\n"
        
        summary += f"\n📄 {job['order_data']['total_pages']}p total"
        if job['order_data'].get('total_sheets'):
            summary += f"\n📋 {job['order_data']['total_sheets']} sheets"
        summary += f"\n💰 *₹{job['order_data']['total_price']}*"
        summary += f"\n\n💳 UPI Payment:\n{payment_url}"
        
        send_whatsapp_text(phone, summary)
        
        # Print order to console
        print("\n" + "="*50)
        print("ORDER JSON:")
        print(json.dumps(job["order_data"], indent=2))
        print("="*50 + "\n")
        
        return jsonify({
            "success": True,
            "payment_url": payment_url,
            "order_id": order_id,
            "total_price": total_price
        })
        
    except Exception as e:
        print(f"❌ Place order error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/orders")
def list_orders():
    """List all orders"""
    try:
        orders = []
        for file_path in ORDERS_DIR.glob("*.json"):
            with open(file_path, 'r') as f:
                orders.append(json.load(f))
        return jsonify({"orders": orders, "count": len(orders)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/orders/<order_id>")
def get_order(order_id):
    """Get specific order"""
    try:
        file_path = ORDERS_DIR / f"{order_id}.json"
        with open(file_path, 'r') as f:
            order_data = json.load(f)
        return jsonify(order_data), 200
    except FileNotFoundError:
        return jsonify({"error": "Order not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 WhatsApp Print Shop Bot Started!")
    print("="*60)
    print(f"📱 WhatsApp Webhook: {NGROK_URL}/webhook")
    print(f"🌐 Web Interface: {NGROK_URL}/order/<session_id>")
    print(f"📊 Orders API: {NGROK_URL}/orders")
    print("="*60 + "\n")
    
    app.run(port=5000, debug=True)