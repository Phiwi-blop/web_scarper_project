#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import re
import json
import csv
import time
import shutil
import urllib.parse
import urllib.request
import subprocess
from datetime import datetime
from threading import Thread

import requests
from bs4 import BeautifulSoup
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QTextEdit, QProgressBar, QComboBox, QCheckBox, 
                            QSpinBox, QTabWidget, QFileDialog, QGroupBox, 
                            QMessageBox, QTreeWidget, QTreeWidgetItem, QHeaderView,
                            QGridLayout, QSpacerItem, QSizePolicy, QSplitter,
                            QScrollArea, QFrame)
from PyQt5.QtCore import Qt, QUrl, pyqtSignal, QObject, QTimer, QSettings
from PyQt5.QtGui import QIcon, QFont, QPalette, QColor


class WorkerSignals(QObject):
    """Defines the signals available for the web scraper thread."""
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    result = pyqtSignal(dict)
    finished = pyqtSignal()
    network_error = pyqtSignal(str, str)  # URL, error message


class ScraperWorker(Thread):
    """Worker thread for web scraping."""
    
    def __init__(self, url, options):
        super().__init__()
        self.url = url
        self.options = options
        self.signals = WorkerSignals()
        self.running = True
        self.daemon = True  # Make thread a daemon thread
        
        # Initialize data containers
        self.results = {
            'links': set(),
            'internal_links': set(),
            'external_links': set(),
            'images': set(),
            'downloaded_images': [],
            'emails': set(),
            'phones': set(),
            'texts': [],
            'forms': [],
            'meta': {},
            'scripts': set(),
            'downloaded_scripts': [],
            'stylesheets': set(),
            'downloaded_stylesheets': [],
            'downloaded_html': [],  # Track downloaded HTML files
            'visited_pages': 0,
            'total_data_size': 0,
            'errors': []  # Track errors during scraping
        }
        
        # Initialize tracking variables
        self.visited_urls = set()
        self.queue = []
        
        # Create download directories if needed
        if (self.options.get('download_images', False) or 
            self.options.get('download_resources', False) or
            self.options.get('download_html', False)):
            self.create_download_directories()
    
    def extract_base_url(self, url):
        """Extract the base URL from a URL."""
        parsed = urllib.parse.urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        return base_url
    
    def is_valid_url(self, url):
        """Check if a URL is valid and should be processed."""
        # Skip non-HTTP URLs
        if not url.startswith(('http://', 'https://')):
            return False
        
        # Skip URLs with unwanted extensions
        unwanted_exts = ['.pdf', '.doc', '.jpg', '.png', '.gif', '.zip']
        if any(url.lower().endswith(ext) for ext in unwanted_exts):
            return False
            
        return True
    
    def normalize_url(self, url, base_url):
        """Normalize a URL by handling relative URLs, etc."""
        # Handle relative URLs
        if url.startswith('/'):
            return urllib.parse.urljoin(base_url, url)
        
        # Handle URLs without scheme
        if not url.startswith(('http://', 'https://')):
            return urllib.parse.urljoin(base_url, url)
            
        return url
    
    def is_internal_link(self, url, base_domain):
        """Check if a URL is internal to the base domain."""
        parsed_url = urllib.parse.urlparse(url)
        url_domain = parsed_url.netloc
        
        return url_domain == base_domain or url_domain.endswith(f".{base_domain}")
    
    def extract_emails(self, text):
        """Extract email addresses from text."""
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        return set(re.findall(email_pattern, text))
    
    def extract_phones(self, text):
        """Extract phone numbers from text."""
        # This is a simple pattern, might need refinement for international numbers
        phone_pattern = r'\+?\d{1,4}?[-.\s]?\(?\d{1,3}?\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}'
        return set(re.findall(phone_pattern, text))
    
    def extract_meta_info(self, soup):
        """Extract meta information from the page."""
        meta = {}
        
        # Extract title
        if soup.title:
            meta['title'] = soup.title.string
        
        # Extract meta tags
        for tag in soup.find_all('meta'):
            name = tag.get('name') or tag.get('property')
            content = tag.get('content')
            if name and content:
                meta[name] = content
                
        return meta
    
    def extract_forms(self, soup, url):
        """Extract forms and their fields from the page."""
        forms = []
        
        for form in soup.find_all('form'):
            form_data = {
                'action': form.get('action', ''),
                'method': form.get('method', 'get').upper(),
                'fields': []
            }
            
            for input_field in form.find_all(['input', 'textarea', 'select']):
                field = {
                    'type': input_field.name,
                    'name': input_field.get('name', ''),
                    'id': input_field.get('id', ''),
                    'required': input_field.get('required') is not None
                }
                
                if input_field.name == 'input':
                    field['input_type'] = input_field.get('type', 'text')
                
                form_data['fields'].append(field)
                
            forms.append(form_data)
            
        return forms
        
    def create_download_directories(self):
        """Create directories for downloaded files."""
        # Ask user for a folder name if not provided
        folder_name = self.options.get('folder_name', '')
        if not folder_name:
            # Generate a default name based on domain and timestamp
            base_domain = urllib.parse.urlparse(self.url).netloc.replace(':', '_')
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            folder_name = f"{base_domain}_{timestamp}"
            
        self.download_dir = os.path.join(
            self.options.get('download_path', 'downloads'),
            folder_name
        )
        
        # Create main download directory
        os.makedirs(self.download_dir, exist_ok=True)
        
        # Create subdirectories for different types of content
        self.images_dir = os.path.join(self.download_dir, 'images')
        self.scripts_dir = os.path.join(self.download_dir, 'scripts')
        self.styles_dir = os.path.join(self.download_dir, 'styles')
        self.html_dir = os.path.join(self.download_dir, 'html')
        
        os.makedirs(self.images_dir, exist_ok=True)
        os.makedirs(self.scripts_dir, exist_ok=True)
        os.makedirs(self.styles_dir, exist_ok=True)
        os.makedirs(self.html_dir, exist_ok=True)
        
        # Create manifest file to keep track of downloads
        self.manifest_path = os.path.join(self.download_dir, 'manifest.json')
        self.manifest = {
            'url': self.url,
            'timestamp': datetime.now().isoformat(),
            'downloads': {
                'images': [],
                'scripts': [],
                'styles': [],
                'html': []
            }
        }
        
        # Inform user of directory creation
        self.signals.status.emit(f"Created download directory: {self.download_dir}")
    
    def download_file(self, url, directory, file_type):
        """Download a file from a URL to a local directory."""
        if not self.running:
            return None
            
        try:
            # Parse URL and get filename
            parsed_url = urllib.parse.urlparse(url)
            filename = os.path.basename(parsed_url.path)
            
            # If no filename or extension, create one
            if not filename or '.' not in filename:
                if file_type == 'image':
                    filename = f"image_{len(self.results['downloaded_images']) + 1}.jpg"
                elif file_type == 'script':
                    filename = f"script_{len(self.results['downloaded_scripts']) + 1}.js"
                elif file_type == 'stylesheet':
                    filename = f"style_{len(self.results['downloaded_stylesheets']) + 1}.css"
                elif file_type == 'html':
                    filename = f"page_{len(self.results.get('downloaded_html', [])) + 1}.html"
            
            # Make sure filename is valid
            filename = re.sub(r'[\\/*?:"<>|]', '_', filename)
            
            # If filename already exists, add a number
            orig_filename = filename
            counter = 1
            while os.path.exists(os.path.join(directory, filename)):
                name, ext = os.path.splitext(orig_filename)
                filename = f"{name}_{counter}{ext}"
                counter += 1
            
            # Construct local file path
            filepath = os.path.join(directory, filename)
            
            # Set headers to mimic a browser
            headers = {
                'User-Agent': self.options.get('user_agent', 'PyQtWebScraper/1.0'),
            }
            
            # Create request with headers
            req = urllib.request.Request(url, headers=headers)
            
            # Download the file
            with urllib.request.urlopen(req, timeout=self.options.get('timeout', 10)) as response, open(filepath, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
            
            # Add to manifest
            if file_type == 'image':
                self.manifest['downloads']['images'].append({
                    'url': url,
                    'local_path': filepath,
                    'timestamp': datetime.now().isoformat()
                })
            elif file_type == 'script':
                self.manifest['downloads']['scripts'].append({
                    'url': url,
                    'local_path': filepath,
                    'timestamp': datetime.now().isoformat()
                })
            elif file_type == 'stylesheet':
                self.manifest['downloads']['styles'].append({
                    'url': url,
                    'local_path': filepath,
                    'timestamp': datetime.now().isoformat()
                })
            elif file_type == 'html':
                self.manifest['downloads']['html'].append({
                    'url': url,
                    'local_path': filepath,
                    'timestamp': datetime.now().isoformat()
                })
                
            # Update manifest file
            with open(self.manifest_path, 'w') as f:
                json.dump(self.manifest, f, indent=2)
            
            return filepath
            
        except Exception as e:
            self.signals.error.emit(f"Error downloading {url}: {str(e)}")
            return None
    
    def scrape_page(self, url):
        """Scrape a single page and extract information based on options."""
        if url in self.visited_urls:
            return
            
        if not self.running:
            return
            
        self.visited_urls.add(url)
        self.signals.status.emit(f"Scraping: {url}")
        
        try:
            # Set custom headers to mimic a browser
            headers = {
                'User-Agent': self.options.get('user_agent', 'PyQtWebScraper/1.0'),
                'Accept': 'text/html,application/xhtml+xml,application/xml',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            
            # Add timeout and retries for robustness
            max_retries = 3
            retry_count = 0
            response = None
            
            while retry_count < max_retries:
                try:
                    response = requests.get(
                        url, 
                        headers=headers, 
                        timeout=self.options.get('timeout', 10),
                        allow_redirects=self.options.get('follow_redirects', True)
                    )
                    response.raise_for_status()  # Raise exception for HTTP errors
                    break
                except requests.exceptions.RequestException as e:
                    retry_count += 1
                    if retry_count >= max_retries:
                        self.signals.network_error.emit(url, str(e))
                        self.results['errors'].append(f"Error accessing {url}: {str(e)}")
                        return
                    # Wait before retrying
                    time.sleep(1)
            
            # If all retries failed
            if response is None:
                return
            
            # Update total data size
            self.results['total_data_size'] += len(response.content)
            
            # Download HTML content if option is enabled
            if self.options.get('download_html', False) and hasattr(self, 'html_dir'):
                page_name = url.split('/')[-1]
                if not page_name or page_name.find('.') == -1:
                    page_name = url.split('//')[-1].replace('/', '_') + '.html'
                
                html_path = os.path.join(self.html_dir, page_name)
                with open(html_path, 'wb') as f:
                    f.write(response.content)
                
                if 'downloaded_html' not in self.results:
                    self.results['downloaded_html'] = []
                
                self.results['downloaded_html'].append({
                    'url': url,
                    'local_path': html_path
                })
            
            # Skip non-HTML responses
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('text/html'):
                return
                
            # Extract base information
            base_url = self.extract_base_url(url)
            base_domain = urllib.parse.urlparse(base_url).netloc
            
            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract links if enabled
            if self.options.get('extract_links', True):
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag.get('href', '').strip()
                    
                    # Skip empty or javascript links
                    if not href or href.startswith(('javascript:', '#')):
                        continue
                        
                    # Normalize the URL
                    full_url = self.normalize_url(href, base_url)
                    
                    if not self.is_valid_url(full_url):
                        continue
                        
                    # Add to appropriate link sets
                    self.results['links'].add(full_url)
                    
                    if self.is_internal_link(full_url, base_domain):
                        self.results['internal_links'].add(full_url)
                        
                        # Add to queue if crawling is enabled and within depth limit
                        if (self.options.get('crawl_pages', False) and 
                            len(self.visited_urls) < self.options.get('max_pages', 10) and 
                            full_url not in self.visited_urls and 
                            full_url not in self.queue):
                            self.queue.append(full_url)
                    else:
                        self.results['external_links'].add(full_url)
            
            # Extract images if enabled
            if self.options.get('extract_images', True):
                for img in soup.find_all('img', src=True):
                    src = img.get('src', '').strip()
                    if src:
                        full_src = self.normalize_url(src, base_url)
                        self.results['images'].add(full_src)
                        
                        # Download image if option is enabled
                        if self.options.get('download_images', False):
                            local_path = self.download_file(full_src, self.images_dir, 'image')
                            if local_path:
                                self.results['downloaded_images'].append({
                                    'url': full_src,
                                    'local_path': local_path
                                })
            
            # Extract text if enabled
            if self.options.get('extract_text', True):
                for p in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                    text = p.get_text(strip=True)
                    if text:
                        self.results['texts'].append({
                            'url': url,
                            'tag': p.name,
                            'text': text
                        })
            
            # Extract emails and phones if enabled
            if self.options.get('extract_contacts', True):
                page_text = soup.get_text()
                self.results['emails'].update(self.extract_emails(page_text))
                self.results['phones'].update(self.extract_phones(page_text))
            
            # Extract meta information if enabled
            if self.options.get('extract_meta', True):
                page_meta = self.extract_meta_info(soup)
                if url not in self.results['meta']:
                    self.results['meta'][url] = page_meta
            
            # Extract forms if enabled
            if self.options.get('extract_forms', True):
                forms = self.extract_forms(soup, url)
                if forms:
                    for form in forms:
                        self.results['forms'].append({
                            'url': url,
                            'form': form
                        })
            
            # Extract scripts and stylesheets if enabled
            if self.options.get('extract_resources', True):
                for script in soup.find_all('script', src=True):
                    src = script.get('src', '').strip()
                    if src:
                        full_src = self.normalize_url(src, base_url)
                        self.results['scripts'].add(full_src)
                        
                        # Download script if option is enabled
                        if self.options.get('download_resources', False):
                            local_path = self.download_file(full_src, self.scripts_dir, 'script')
                            if local_path:
                                self.results['downloaded_scripts'].append({
                                    'url': full_src,
                                    'local_path': local_path
                                })
                
                for link in soup.find_all('link', rel='stylesheet', href=True):
                    href = link.get('href', '').strip()
                    if href:
                        full_href = self.normalize_url(href, base_url)
                        self.results['stylesheets'].add(full_href)
                        
                        # Download stylesheet if option is enabled
                        if self.options.get('download_resources', False):
                            local_path = self.download_file(full_href, self.styles_dir, 'stylesheet')
                            if local_path:
                                self.results['downloaded_stylesheets'].append({
                                    'url': full_href,
                                    'local_path': local_path
                                })
            
            # Increment visited pages counter
            self.results['visited_pages'] += 1
            
            # Report progress
            if self.options.get('max_pages', 10) > 0:
                progress = min(100, int((self.results['visited_pages'] / self.options.get('max_pages', 10)) * 100))
                self.signals.progress.emit(progress)
            
            # Sleep to avoid overloading the server
            time.sleep(self.options.get('delay', 1))
            
        except Exception as e:
            self.signals.error.emit(f"Error scraping {url}: {str(e)}")
    
    def run(self):
        """Run the scraper thread."""
        try:
            # Add initial URL to the queue
            self.queue.append(self.url)
            
            # Process URLs until queue is empty or max pages reached
            while (self.queue and self.running and 
                  len(self.visited_urls) < self.options.get('max_pages', 10)):
                try:
                    next_url = self.queue.pop(0)
                    self.scrape_page(next_url)
                except Exception as page_error:
                    self.signals.error.emit(f"Error processing {next_url}: {str(page_error)}")
                    self.results['errors'].append(f"Error processing {next_url}: {str(page_error)}")
                    # Continue with next URL instead of crashing
                    continue
            
            # Convert sets to lists for JSON serialization
            if not self.running:
                self.signals.status.emit("Scraping stopped by user")
                self.signals.finished.emit()
                return
                
            result_dict = {
                'links': list(self.results['links']),
                'internal_links': list(self.results['internal_links']),
                'external_links': list(self.results['external_links']),
                'images': list(self.results['images']),
                'emails': list(self.results['emails']),
                'phones': list(self.results['phones']),
                'texts': self.results['texts'],
                'forms': self.results['forms'],
                'meta': self.results['meta'],
                'scripts': list(self.results['scripts']),
                'stylesheets': list(self.results['stylesheets']),
                'visited_pages': self.results['visited_pages'],
                'total_data_size': self.results['total_data_size'],
                'downloaded_images': self.results.get('downloaded_images', []),
                'downloaded_scripts': self.results.get('downloaded_scripts', []),
                'downloaded_stylesheets': self.results.get('downloaded_stylesheets', []),
                'downloaded_html': self.results.get('downloaded_html', []),
                'errors': self.results.get('errors', [])
            }
            
            self.signals.result.emit(result_dict)
            self.signals.status.emit("Scraping completed")
            
        except Exception as e:
            self.signals.error.emit(f"Error in scraper thread: {str(e)}")
        finally:
            self.signals.finished.emit()
    
    def stop(self):
        """Stop the scraper thread."""
        self.running = False


class WebScraperApp(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.scraper_thread = None
        self.results = None
        self.download_path = os.path.join(os.path.expanduser("~"), "downloads")
        self.consecutive_errors = 0
        self.max_consecutive_errors = 3
        self.init_ui()
        
        # Configure window properties for better responsiveness
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Restore window geometry from settings if available
        self.restore_geometry()
    
    def init_ui(self):
        """Initialize the user interface with improved responsive layout."""
        self.setWindowTitle("Web Scraper")
        self.setGeometry(100, 100, 1200, 800)
        
        # Apply the same styles as before
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #f8f9fa;
                color: #343a40;
            }
            QLineEdit, QComboBox {
                padding: 8px;
                border: 1px solid #ced4da;
                border-radius: 4px;
                background-color: white;
                selection-background-color: #4dabf7;
            }
            QPushButton {
                background-color: #4dabf7;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #339af0;
            }
            QPushButton:disabled {
                background-color: #adb5bd;
            }
            QPushButton#stopButton {
                background-color: #fa5252;
            }
            QPushButton#stopButton:hover {
                background-color: #e03131;
            }
            QGroupBox {
                border: 1px solid #ced4da;
                border-radius: 6px;
                margin-top: 12px;
                font-weight: bold;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            /* Additional styles for the collapsible section */
            QPushButton#toggleButton {
                background-color: transparent;
                color: #4dabf7;
                border: none;
                padding: 4px;
                font-weight: bold;
                font-size: 10pt;
                text-align: left;
            }
            QSplitter::handle {
                background-color: #ced4da;
            }
            QSplitter::handle:horizontal {
                width: 4px;
            }
            QSplitter::handle:vertical {
                height: 4px;
            }
        """)
        
        # Create main widget and layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # Create a horizontal splitter to divide the UI
        self.main_splitter = QSplitter(Qt.Vertical)
        
        # Create top panel for URL input and essential controls
        top_panel = QWidget()
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)
        
        # URL input section
        url_layout = QHBoxLayout()
        url_layout.setSpacing(10)
        
        url_label = QLabel("URL:")
        url_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter website URL, domain or IP (e.g., https://example.com)")
        
        self.start_button = QPushButton("Start")
        self.start_button.setIcon(QIcon.fromTheme("media-playback-start"))
        self.start_button.clicked.connect(self.start_scraping)
        self.start_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setIcon(QIcon.fromTheme("media-playback-stop"))
        self.stop_button.clicked.connect(self.stop_scraping)
        self.stop_button.setEnabled(False)
        self.stop_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input, 1)
        url_layout.addWidget(self.start_button)
        url_layout.addWidget(self.stop_button)
        
        top_layout.addLayout(url_layout)
        
        # Status and progress section
        status_layout = QHBoxLayout()
        
        self.status_label = QLabel("Ready to start scraping")
        self.status_label.setStyleSheet("font-weight: bold;")
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        
        status_layout.addWidget(self.status_label, 1)
        status_layout.addWidget(self.progress_bar, 2)
        
        top_layout.addLayout(status_layout)
        
        # Options section (always visible)
        options_container = QWidget()
        options_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        
        self.options_layout = QVBoxLayout(options_container)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(10)
        
        # Options panel
        options_panel_layout = QVBoxLayout()
        options_panel_layout.setContentsMargins(0, 0, 0, 0)
        options_panel_layout.setSpacing(10)
        
        # Horizontal layout for options
        options_horizontal = QHBoxLayout()
        options_horizontal.setSpacing(10)
        
        # Left column - Content options
        extraction_group = QGroupBox("Content to Extract")
        extraction_layout = QGridLayout(extraction_group)
        extraction_layout.setSpacing(8)
        
        self.extract_links_check = QCheckBox("Links")
        self.extract_links_check.setChecked(True)
        extraction_layout.addWidget(self.extract_links_check, 0, 0)
        
        self.extract_images_check = QCheckBox("Images")
        self.extract_images_check.setChecked(True)
        extraction_layout.addWidget(self.extract_images_check, 1, 0)
        
        self.extract_text_check = QCheckBox("Text")
        self.extract_text_check.setChecked(True)
        extraction_layout.addWidget(self.extract_text_check, 2, 0)
        
        self.extract_contacts_check = QCheckBox("Contacts")
        self.extract_contacts_check.setChecked(True)
        extraction_layout.addWidget(self.extract_contacts_check, 0, 1)
        
        self.extract_meta_check = QCheckBox("Meta Info")
        self.extract_meta_check.setChecked(True)
        extraction_layout.addWidget(self.extract_meta_check, 1, 1)
        
        self.extract_forms_check = QCheckBox("Forms")
        self.extract_forms_check.setChecked(True)
        extraction_layout.addWidget(self.extract_forms_check, 2, 1)
        
        self.extract_resources_check = QCheckBox("Resources")
        self.extract_resources_check.setChecked(True)
        extraction_layout.addWidget(self.extract_resources_check, 0, 2)
        
        self.crawl_pages_check = QCheckBox("Crawl Pages")
        self.crawl_pages_check.setChecked(True)
        extraction_layout.addWidget(self.crawl_pages_check, 1, 2)
        
        options_horizontal.addWidget(extraction_group)
        
        # Middle column - Download options
        download_group = QGroupBox("Download Options")
        download_layout = QVBoxLayout(download_group)
        download_layout.setSpacing(8)
        
        download_checkboxes = QHBoxLayout()
        
        self.download_images_check = QCheckBox("Download Images")
        self.download_images_check.setChecked(False)
        download_checkboxes.addWidget(self.download_images_check)
        
        self.download_resources_check = QCheckBox("Download Code")
        self.download_resources_check.setChecked(False)
        download_checkboxes.addWidget(self.download_resources_check)
        
        self.download_html_check = QCheckBox("Download HTML")
        self.download_html_check.setChecked(False)
        download_checkboxes.addWidget(self.download_html_check)
        
        download_layout.addLayout(download_checkboxes)
        
        folder_layout = QHBoxLayout()
        folder_name_label = QLabel("Folder:")
        folder_layout.addWidget(folder_name_label)
        
        self.folder_name_input = QLineEdit()
        self.folder_name_input.setPlaceholderText("Auto-generated if empty")
        folder_layout.addWidget(self.folder_name_input)
        
        self.download_path_button = QPushButton("Path")
        self.download_path_button.setIcon(QIcon.fromTheme("folder"))
        self.download_path_button.clicked.connect(self.set_download_path)
        self.download_path_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        folder_layout.addWidget(self.download_path_button)
        
        download_layout.addLayout(folder_layout)
        options_horizontal.addWidget(download_group)
        
        # Right column - Advanced options
        advanced_group = QGroupBox("Advanced Options")
        advanced_layout = QGridLayout(advanced_group)
        advanced_layout.setSpacing(8)
        
        # First row
        max_pages_label = QLabel("Max Pages:")
        advanced_layout.addWidget(max_pages_label, 0, 0)
        
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setMinimum(1)
        self.max_pages_spin.setMaximum(1000)
        self.max_pages_spin.setValue(10)
        self.max_pages_spin.setSingleStep(5)
        advanced_layout.addWidget(self.max_pages_spin, 0, 1)
        
        delay_label = QLabel("Delay (s):")
        advanced_layout.addWidget(delay_label, 0, 2)
        
        self.delay_spin = QSpinBox()
        self.delay_spin.setMinimum(0)
        self.delay_spin.setMaximum(10)
        self.delay_spin.setValue(1)
        advanced_layout.addWidget(self.delay_spin, 0, 3)
        
        # Second row
        timeout_label = QLabel("Timeout (s):")
        advanced_layout.addWidget(timeout_label, 1, 0)
        
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setMinimum(1)
        self.timeout_spin.setMaximum(30)
        self.timeout_spin.setValue(10)
        advanced_layout.addWidget(self.timeout_spin, 1, 1)
        
        user_agent_label = QLabel("User Agent:")
        advanced_layout.addWidget(user_agent_label, 1, 2)
        
        self.user_agent_combo = QComboBox()
        self.user_agent_combo.addItems([
            "PyQtWebScraper/1.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
        ])
        advanced_layout.addWidget(self.user_agent_combo, 1, 3)
        
        options_horizontal.addWidget(advanced_group)
        
        # Add the horizontal layout to the options panel
        options_panel_layout.addLayout(options_horizontal)
        
        # Remove export layout from options panel
        
        # Add the options layout directly to the container
        self.options_layout.addLayout(options_panel_layout)
        
        # Add the options container to the top panel
        top_layout.addWidget(options_container)
        
        # Add top panel to splitter
        self.main_splitter.addWidget(top_panel)
        
        # Create results tab widget with additional top margin
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setContentsMargins(0, 10, 0, 0)  # Add top margin
        
        # Add a horizontal separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setStyleSheet("background-color: #ced4da; max-height: 1px;")
        self.main_splitter.addWidget(separator)
        
        # Summary tab
        summary_widget = QWidget()
        summary_layout = QVBoxLayout(summary_widget)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setStyleSheet("font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.5;")
        summary_layout.addWidget(self.summary_text)
        self.tabs.addTab(summary_widget, "Summary")
        
        # Links tab
        links_widget = QWidget()
        links_layout = QVBoxLayout(links_widget)
        self.links_tree = QTreeWidget()
        self.links_tree.setHeaderLabels(["URL"])
        self.links_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self.links_tree.setAlternatingRowColors(True)
        links_layout.addWidget(self.links_tree)
        self.tabs.addTab(links_widget, "Links")
        
        # Images tab
        images_widget = QWidget()
        images_layout = QVBoxLayout(images_widget)
        self.images_tree = QTreeWidget()
        self.images_tree.setHeaderLabels(["URL"])
        self.images_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self.images_tree.setAlternatingRowColors(True)
        images_layout.addWidget(self.images_tree)
        self.tabs.addTab(images_widget, "Images")
        
        # Text tab
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        self.text_tree = QTreeWidget()
        self.text_tree.setHeaderLabels(["Page", "Tag", "Text"])
        self.text_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.text_tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.text_tree.setAlternatingRowColors(True)
        text_layout.addWidget(self.text_tree)
        self.tabs.addTab(text_widget, "Text")
        
        # Contacts tab
        contacts_widget = QWidget()
        contacts_layout = QVBoxLayout(contacts_widget)
        self.contacts_tree = QTreeWidget()
        self.contacts_tree.setHeaderLabels(["Type", "Value"])
        self.contacts_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self.contacts_tree.setAlternatingRowColors(True)
        contacts_layout.addWidget(self.contacts_tree)
        self.tabs.addTab(contacts_widget, "Contacts")
        
        # Meta Info tab
        meta_widget = QWidget()
        meta_layout = QVBoxLayout(meta_widget)
        self.meta_tree = QTreeWidget()
        self.meta_tree.setHeaderLabels(["Page", "Name", "Content"])
        self.meta_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.meta_tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.meta_tree.setAlternatingRowColors(True)
        meta_layout.addWidget(self.meta_tree)
        self.tabs.addTab(meta_widget, "Meta Info")
        
        # Forms tab
        forms_widget = QWidget()
        forms_layout = QVBoxLayout(forms_widget)
        self.forms_tree = QTreeWidget()
        self.forms_tree.setHeaderLabels(["Page", "Action", "Method", "Fields"])
        self.forms_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.forms_tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.forms_tree.setAlternatingRowColors(True)
        forms_layout.addWidget(self.forms_tree)
        self.tabs.addTab(forms_widget, "Forms")
        
        # Resources tab
        resources_widget = QWidget()
        resources_layout = QVBoxLayout(resources_widget)
        self.resources_tree = QTreeWidget()
        self.resources_tree.setHeaderLabels(["Type", "URL"])
        self.resources_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self.resources_tree.setAlternatingRowColors(True)
        resources_layout.addWidget(self.resources_tree)
        self.tabs.addTab(resources_widget, "Resources")
        
        # Errors tab
        errors_widget = QWidget()
        errors_layout = QVBoxLayout(errors_widget)
        self.errors_tree = QTreeWidget()
        self.errors_tree.setHeaderLabels(["Error"])
        self.errors_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        self.errors_tree.setAlternatingRowColors(True)
        errors_layout.addWidget(self.errors_tree)
        self.tabs.addTab(errors_widget, "Errors")
        
        # Downloads tab
        downloads_widget = QWidget()
        downloads_layout = QVBoxLayout(downloads_widget)
        self.downloads_tree = QTreeWidget()
        self.downloads_tree.setHeaderLabels(["Type", "URL", "Local Path"])
        self.downloads_tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.downloads_tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.downloads_tree.setAlternatingRowColors(True)
        downloads_layout.addWidget(self.downloads_tree)
        self.tabs.addTab(downloads_widget, "Downloads")
        
        # Add tabs to splitter
        self.main_splitter.addWidget(self.tabs)
        
        # Set initial sizes for the splitter (top panel, separator, results panel)
        self.main_splitter.setSizes([200, 30, 570])  # Allocate space for options, separator, and results
        
        # Add splitter to main layout
        main_layout.addWidget(self.main_splitter)
        
        # Add export options at the bottom of the UI
        export_section = QWidget()
        export_layout = QHBoxLayout(export_section)
        export_layout.setContentsMargins(0, 10, 0, 0)
        export_layout.setSpacing(10)
        
        export_format_label = QLabel("Export Format:")
        export_layout.addWidget(export_format_label)
        
        self.export_combo = QComboBox()
        self.export_combo.addItems(["JSON", "CSV", "TXT", "HTML"])
        export_layout.addWidget(self.export_combo)
        
        self.export_button = QPushButton("Export Results")
        self.export_button.setIcon(QIcon.fromTheme("document-save"))
        self.export_button.clicked.connect(self.export_results)
        self.export_button.setEnabled(False)
        export_layout.addWidget(self.export_button)
        
        export_layout.addStretch()
        
        main_layout.addWidget(export_section)
        
        # Set central widget
        self.setCentralWidget(main_widget)
    
    # Removed toggle_options method as options are now always visible
    
    def restore_geometry(self):
        """Restore window geometry from saved settings."""
        try:
            settings = QSettings("PyQtWebScraper", "WebScraper")
            geometry = settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
            
            # Restore splitter sizes if available
            splitter_sizes = settings.value("splitter_sizes")
            if splitter_sizes and hasattr(self, 'main_splitter'):
                self.main_splitter.setSizes(splitter_sizes)
                
            # Options panel state no longer needed as it's always visible
                
        except Exception as e:
            print(f"Error restoring geometry: {e}")
    
    def save_geometry(self):
        """Save window geometry and settings."""
        try:
            settings = QSettings("PyQtWebScraper", "WebScraper")
            settings.setValue("geometry", self.saveGeometry())
            
            # Save splitter sizes
            if hasattr(self, 'main_splitter'):
                settings.setValue("splitter_sizes", self.main_splitter.sizes())
                
            # Options panel state no longer needed
                
        except Exception as e:
            print(f"Error saving geometry: {e}")
    
    def resizeEvent(self, event):
        """Handle window resize event."""
        super().resizeEvent(event)
        # When the window is resized, adjust the splitter if needed
        if hasattr(self, 'main_splitter'):
            # You could dynamically adjust the splitter based on window size
            if self.width() < 800:  # Compact mode for small windows
                self.main_splitter.setSizes([100, self.height() - 100])
    
    def set_download_path(self):
        """Set download path for files."""
        download_dir = QFileDialog.getExistingDirectory(
            self, "Select Download Directory", os.path.expanduser("~"),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        
        if download_dir:
            self.download_path = download_dir
            self.status_label.setText(f"Download path set to: {download_dir}")
            
            # Show the current path in a tooltip on the button
            self.download_path_button.setToolTip(f"Current: {download_dir}")
            
            # Highlight the button briefly to show success
            original_style = self.download_path_button.styleSheet()
            self.download_path_button.setStyleSheet("background-color: #28a745; color: white;")
            QTimer.singleShot(1000, lambda: self.download_path_button.setStyleSheet(original_style))
    
    def start_scraping(self):
        """Start the scraping process."""
        url = self.url_input.text().strip()
        
        if not url:
            QMessageBox.warning(self, "Input Error", "Please enter a URL")
            return
            
        # Add http:// prefix if missing
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
            self.url_input.setText(url)
        
        # Check if a scraper thread is already running
        if self.scraper_thread and self.scraper_thread.is_alive():
            reply = QMessageBox.question(
                self, 
                "Scraper Already Running", 
                "A scraping process is already running. Do you want to stop it and start a new one?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.stop_scraping()
                # Wait a moment for the thread to clean up
                time.sleep(0.5)
            else:
                return
        
        # Get scraping options
        options = {
            'extract_links': self.extract_links_check.isChecked(),
            'extract_images': self.extract_images_check.isChecked(),
            'extract_text': self.extract_text_check.isChecked(),
            'extract_contacts': self.extract_contacts_check.isChecked(),
            'extract_meta': self.extract_meta_check.isChecked(),
            'extract_forms': self.extract_forms_check.isChecked(),
            'extract_resources': self.extract_resources_check.isChecked(),
            'crawl_pages': self.crawl_pages_check.isChecked(),
            'download_images': self.download_images_check.isChecked(),
            'download_resources': self.download_resources_check.isChecked(),
            'download_html': self.download_html_check.isChecked(),
            'folder_name': self.folder_name_input.text().strip(),
            'max_pages': self.max_pages_spin.value(),
            'delay': self.delay_spin.value(),
            'timeout': self.timeout_spin.value(),
            'user_agent': self.user_agent_combo.currentText(),
            'follow_redirects': True
        }
        
        # Set download path if available
        if hasattr(self, 'download_path'):
            options['download_path'] = self.download_path
        
        # Clear previous results
        self.clear_results()
        
        # Reset error count
        self.consecutive_errors = 0
        
        # Create and start worker thread
        self.scraper_thread = ScraperWorker(url, options)
        self.scraper_thread.signals.progress.connect(self.update_progress)
        self.scraper_thread.signals.status.connect(self.update_status)
        self.scraper_thread.signals.error.connect(self.show_error)
        self.scraper_thread.signals.network_error.connect(self.handle_network_error)
        self.scraper_thread.signals.result.connect(self.display_results)
        self.scraper_thread.signals.finished.connect(self.scraping_finished)
        
        # Update UI
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting...")
        
        # Start the thread
        self.scraper_thread.start()
    
    def stop_scraping(self):
        """Stop the scraping process."""
        if self.scraper_thread and self.scraper_thread.is_alive():
            self.scraper_thread.stop()
            self.update_status("Stopping... Please wait")
            
            # Set a timeout for the thread to stop
            def check_thread_stopped():
                if self.scraper_thread and self.scraper_thread.is_alive():
                    # Thread is still running after timeout, force UI to be responsive
                    self.start_button.setEnabled(True)
                    self.stop_button.setEnabled(False)
                    self.status_label.setText("Scraper taking too long to stop. UI unlocked.")
                    
            # Check after 5 seconds if thread has stopped
            QTimer.singleShot(5000, check_thread_stopped)
    
    def scraping_finished(self):
        """Handle scraping completion."""
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.export_button.setEnabled(True if self.results else False)
        
        # Clean up thread reference
        if self.scraper_thread:
            self.scraper_thread = None
    
    def update_progress(self, value):
        """Update progress bar."""
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(f"{value}% complete")
    
    def update_status(self, message):
        """Update status label."""
        self.status_label.setText(message)
        # Flash status label to draw attention
        current_style = self.status_label.styleSheet()
        self.status_label.setStyleSheet("font-weight: bold; color: #4dabf7;")
        QApplication.processEvents()
        QApplication.instance().processEvents()
        # Reset style after a short delay
        def reset_style():
            self.status_label.setStyleSheet("font-weight: bold;")
        QApplication.instance().processEvents()
        QApplication.processEvents()
    
    def handle_network_error(self, url, error_message):
        """Handle network errors during scraping."""
        self.consecutive_errors += 1
        self.show_error(f"Network error for {url}: {error_message}")
        
        # If too many consecutive errors, suggest stopping
        if self.consecutive_errors >= self.max_consecutive_errors:
            if self.scraper_thread and self.scraper_thread.is_alive():
                reply = QMessageBox.question(
                    self, 
                    "Multiple Errors Detected", 
                    f"Encountered {self.consecutive_errors} consecutive errors. Do you want to stop scraping?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                
                if reply == QMessageBox.Yes:
                    self.stop_scraping()
    
    def show_error(self, message):
        """Show error message."""
        self.status_label.setText(f"Error: {message}")
        self.status_label.setStyleSheet("font-weight: bold; color: #fa5252;")
        
        # Log error to console for debugging
        print(f"ERROR: {message}")
        
        # Reset error styles after 5 seconds
        QTimer.singleShot(5000, lambda: self.status_label.setStyleSheet("font-weight: bold;"))
    
    def clear_results(self):
        """Clear all result displays."""
        self.summary_text.clear()
        self.links_tree.clear()
        self.images_tree.clear()
        self.text_tree.clear()
        self.contacts_tree.clear()
        self.meta_tree.clear()
        self.forms_tree.clear()
        self.resources_tree.clear()
        self.downloads_tree.clear()
        self.errors_tree.clear()
        self.results = None
    
    def display_errors(self, errors):
        """Display errors in the errors tab."""
        # Clear previous errors
        self.errors_tree.clear()
        
        # Add errors to the tree
        for error in errors:
            QTreeWidgetItem(self.errors_tree, [error])
            
        # Select the errors tab if there are errors
        if errors:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == "Errors":
                    self.tabs.setCurrentIndex(i)
                    break
    
    def display_results(self, results):
        """Display scraping results in the UI."""
        self.results = results
        
        # Reset consecutive error count on successful results
        self.consecutive_errors = 0
        
        # Display errors tab if there are errors
        if results.get('errors') and len(results.get('errors')) > 0:
            self.display_errors(results.get('errors', []))
        
        # Display summary with modern formatting
        summary = f"""
        <h2 style="color: #212529; font-family: 'Segoe UI', Arial, sans-serif;">Scraping Results Summary</h2>
        
        <div style="margin: 20px 0; padding: 15px; background-color: #e9ecef; border-radius: 5px;">
            <p style="margin: 5px 0; font-weight: bold;">Pages Visited: {results['visited_pages']}</p>
            <p style="margin: 5px 0; font-weight: bold;">Total Data Size: {self.format_size(results['total_data_size'])}</p>
            <p style="margin: 5px 0; font-weight: bold;">Download Location: {getattr(self.scraper_thread, 'download_dir', 'None')}</p>
        </div>
        
        <h3 style="color: #495057; margin-top: 20px;">Content Statistics</h3>
        
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-top: 10px;">
            <div style="padding: 15px; background-color: #f1f9ff; border-radius: 5px; border-left: 4px solid #4dabf7;">
                <p style="font-weight: bold; color: #212529; margin: 0 0 10px 0;">Links</p>
                <p style="margin: 5px 0;">Total Links: {len(results['links'])}</p>
                <p style="margin: 5px 0;">- Internal Links: {len(results['internal_links'])}</p>
                <p style="margin: 5px 0;">- External Links: {len(results['external_links'])}</p>
            </div>
            
            <div style="padding: 15px; background-color: #fff4e6; border-radius: 5px; border-left: 4px solid #fd7e14;">
                <p style="font-weight: bold; color: #212529; margin: 0 0 10px 0;">Media</p>
                <p style="margin: 5px 0;">Images Found: {len(results['images'])}</p>
                <p style="margin: 5px 0;">- Downloaded Images: {len(results.get('downloaded_images', []))}</p>
                <p style="margin: 5px 0;">- Downloaded HTML: {len(results.get('downloaded_html', []))}</p>
            </div>
            
            <div style="padding: 15px; background-color: #f3f0ff; border-radius: 5px; border-left: 4px solid #7950f2;">
                <p style="font-weight: bold; color: #212529; margin: 0 0 10px 0;">Content</p>
                <p style="margin: 5px 0;">Text Elements: {len(results['texts'])}</p>
                <p style="margin: 5px 0;">Forms Found: {len(results['forms'])}</p>
            </div>
            
            <div style="padding: 15px; background-color: #e6fcf5; border-radius: 5px; border-left: 4px solid #20c997;">
                <p style="font-weight: bold; color: #212529; margin: 0 0 10px 0;">Contacts</p>
                <p style="margin: 5px 0;">Emails: {len(results['emails'])}</p>
                <p style="margin: 5px 0;">Phone Numbers: {len(results['phones'])}</p>
            </div>
        </div>
        
        <h3 style="color: #495057; margin-top: 20px;">Resources</h3>
        <div style="padding: 15px; background-color: #f8f9fa; border-radius: 5px; margin-top: 10px;">
            <p style="margin: 5px 0;">Scripts: {len(results['scripts'])}</p>
            <p style="margin: 5px 0;">- Downloaded Scripts: {len(results.get('downloaded_scripts', []))}</p>
            <p style="margin: 5px 0;">Stylesheets: {len(results['stylesheets'])}</p>
            <p style="margin: 5px 0;">- Downloaded Stylesheets: {len(results.get('downloaded_stylesheets', []))}</p>
        </div>
        """
        
        self.summary_text.setText(summary)
        
        # If any downloads were performed, ask if user wants to open the download folder
        has_downloads = (
            len(results.get('downloaded_images', [])) > 0 or
            len(results.get('downloaded_scripts', [])) > 0 or
            len(results.get('downloaded_stylesheets', [])) > 0 or
            len(results.get('downloaded_html', [])) > 0
        )
        
        if has_downloads and hasattr(self.scraper_thread, 'download_dir'):
            download_dir = self.scraper_thread.download_dir
            
            # Find the Downloads tab and select it
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == "Downloads":
                    self.tabs.setCurrentIndex(i)
                    break
            
            # Ask if user wants to open the folder
            reply = QMessageBox.question(
                self, 
                "Downloads Complete", 
                f"Files have been downloaded to:\n{download_dir}\n\nWould you like to open this folder?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                self.open_download_folder()
        
        # Display links
        self.links_tree.clear()
        internal_category = QTreeWidgetItem(self.links_tree, ["Internal Links"])
        for link in sorted(results['internal_links']):
            QTreeWidgetItem(internal_category, [link])
        
        external_category = QTreeWidgetItem(self.links_tree, ["External Links"])
        for link in sorted(results['external_links']):
            QTreeWidgetItem(external_category, [link])
        
        self.links_tree.expandAll()
        
        # Display images
        self.images_tree.clear()
        for image in sorted(results['images']):
            QTreeWidgetItem(self.images_tree, [image])
        
        # Display texts
        self.text_tree.clear()
        for text_item in results['texts']:
            QTreeWidgetItem(self.text_tree, [
                text_item['url'],
                text_item['tag'],
                text_item['text']
            ])
        
        # Display contacts
        self.contacts_tree.clear()
        email_category = QTreeWidgetItem(self.contacts_tree, ["Emails"])
        for email in sorted(results['emails']):
            QTreeWidgetItem(email_category, ["Email", email])
        
        phone_category = QTreeWidgetItem(self.contacts_tree, ["Phone Numbers"])
        for phone in sorted(results['phones']):
            QTreeWidgetItem(phone_category, ["Phone", phone])
        
        self.contacts_tree.expandAll()
        
        # Display meta info
        self.meta_tree.clear()
        for url, meta_dict in results['meta'].items():
            url_item = QTreeWidgetItem(self.meta_tree, [url])
            for key, value in meta_dict.items():
                QTreeWidgetItem(url_item, ["", key, str(value)])
        
        # Display forms
        self.forms_tree.clear()
        for form_data in results['forms']:
            url = form_data['url']
            form = form_data['form']
            
            fields_text = ", ".join([
                f"{field['name']} ({field.get('input_type', field['type'])})"
                for field in form['fields']
                if field.get('name')
            ])
            
            QTreeWidgetItem(self.forms_tree, [
                url,
                form['action'],
                form['method'],
                fields_text
            ])
        
        # Display resources
        self.resources_tree.clear()
        scripts_category = QTreeWidgetItem(self.resources_tree, ["Scripts"])
        for script in sorted(results['scripts']):
            QTreeWidgetItem(scripts_category, ["Script", script])
        
        stylesheets_category = QTreeWidgetItem(self.resources_tree, ["Stylesheets"])
        for stylesheet in sorted(results['stylesheets']):
            QTreeWidgetItem(stylesheets_category, ["Stylesheet", stylesheet])
        
        self.resources_tree.expandAll()
        
        # Display downloads
        self.downloads_tree.clear()
        
        # HTML category
        if results.get('downloaded_html'):
            html_category = QTreeWidgetItem(self.downloads_tree, ["Downloaded HTML"])
            for html in results['downloaded_html']:
                QTreeWidgetItem(html_category, ["HTML", html['url'], html['local_path']])
        
        # Images category
        if results.get('downloaded_images'):
            images_category = QTreeWidgetItem(self.downloads_tree, ["Downloaded Images"])
            for image in results['downloaded_images']:
                QTreeWidgetItem(images_category, ["Image", image['url'], image['local_path']])
        
        # Scripts category
        if results.get('downloaded_scripts'):
            scripts_download_category = QTreeWidgetItem(self.downloads_tree, ["Downloaded Scripts"])
            for script in results['downloaded_scripts']:
                QTreeWidgetItem(scripts_download_category, ["Script", script['url'], script['local_path']])
        
        # Stylesheets category
        if results.get('downloaded_stylesheets'):
            styles_download_category = QTreeWidgetItem(self.downloads_tree, ["Downloaded Stylesheets"])
            for style in results['downloaded_stylesheets']:
                QTreeWidgetItem(styles_download_category, ["Stylesheet", style['url'], style['local_path']])
        
        self.downloads_tree.expandAll()
        
        # Show download folder button if downloads exist
        if (results.get('downloaded_html') or 
            results.get('downloaded_images') or 
            results.get('downloaded_scripts') or 
            results.get('downloaded_stylesheets')):
            
            # Create or find the open folder button
            if not hasattr(self, 'open_folder_button'):
                self.open_folder_button = QPushButton("Open Download Folder")
                self.open_folder_button.setIcon(QIcon.fromTheme("folder-open"))
                self.open_folder_button.clicked.connect(self.open_download_folder)
                for i in range(self.tabs.count()):
                    if self.tabs.tabText(i) == "Downloads":
                        downloads_widget = self.tabs.widget(i)
                        if isinstance(downloads_widget, QWidget):
                            downloads_layout = downloads_widget.layout()
                            if downloads_layout:
                                # Add a proper spacer with QSizePolicy
                                spacer = QSpacerItem(20, 10, QSizePolicy.Minimum, QSizePolicy.Expanding)
                                downloads_layout.addItem(spacer)
                                downloads_layout.addWidget(self.open_folder_button)
            
            self.open_folder_button.setEnabled(True)
    
    def open_download_folder(self):
        """Open the download folder in file explorer."""
        if hasattr(self.scraper_thread, 'download_dir') and self.scraper_thread.download_dir:
            download_dir = self.scraper_thread.download_dir
            if os.path.exists(download_dir):
                # Open folder in file explorer (cross-platform)
                if sys.platform == 'win32':
                    os.startfile(download_dir)
                elif sys.platform == 'darwin':  # macOS
                    subprocess.Popen(['open', download_dir])
                else:  # Linux
                    subprocess.Popen(['xdg-open', download_dir])
            else:
                QMessageBox.warning(self, "Folder Not Found", 
                                  f"The download folder '{download_dir}' does not exist.")
        else:
            QMessageBox.warning(self, "No Downloads", 
                              "No download folder is available for this session.")
    
    def format_size(self, size_bytes):
        """Format size in bytes to a human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"
    
    def export_results(self):
        """Export results to a file."""
        if not self.results:
            QMessageBox.warning(self, "Export Error", "No results to export")
            return
            
        export_format = self.export_combo.currentText()
        
        # Get file path from user
        default_name = f"web_scrape_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        if export_format == "JSON":
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save JSON", f"{default_name}.json", "JSON Files (*.json)")
        elif export_format == "CSV":
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save CSV", f"{default_name}.csv", "CSV Files (*.csv)")
        elif export_format == "TXT":
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save TXT", f"{default_name}.txt", "Text Files (*.txt)")
        elif export_format == "HTML":
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save HTML", f"{default_name}.html", "HTML Files (*.html)")
        else:
            return
            
        if not file_path:
            return
            
        try:
            if export_format == "JSON":
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(self.results, f, indent=2)
                    
            elif export_format == "CSV":
                with open(file_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    
                    # Write links
                    writer.writerow(["LINKS"])
                    writer.writerow(["URL", "Type"])
                    for link in self.results['internal_links']:
                        writer.writerow([link, "Internal"])
                    for link in self.results['external_links']:
                        writer.writerow([link, "External"])
                    writer.writerow([])
                    
                    # Write images
                    writer.writerow(["IMAGES"])
                    writer.writerow(["URL"])
                    for image in self.results['images']:
                        writer.writerow([image])
                    writer.writerow([])
                    
                    # Write texts
                    writer.writerow(["TEXTS"])
                    writer.writerow(["URL", "Tag", "Text"])
                    for text_item in self.results['texts']:
                        writer.writerow([
                            text_item['url'],
                            text_item['tag'],
                            text_item['text']
                        ])
                    writer.writerow([])
                    
                    # Write contacts
                    writer.writerow(["CONTACTS"])
                    writer.writerow(["Type", "Value"])
                    for email in self.results['emails']:
                        writer.writerow(["Email", email])
                    for phone in self.results['phones']:
                        writer.writerow(["Phone", phone])
                    
            elif export_format == "TXT":
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write("Web Scraper Results\n")
                    f.write("==================\n\n")
                    
                    f.write(f"Pages Visited: {self.results['visited_pages']}\n")
                    f.write(f"Total Data Size: {self.format_size(self.results['total_data_size'])}\n\n")
                    
                    f.write("LINKS\n-----\n")
                    f.write("\nInternal Links:\n")
                    for link in sorted(self.results['internal_links']):
                        f.write(f"- {link}\n")
                    f.write("\nExternal Links:\n")
                    for link in sorted(self.results['external_links']):
                        f.write(f"- {link}\n")
                    
                    f.write("\nIMAGES\n------\n")
                    for image in sorted(self.results['images']):
                        f.write(f"- {image}\n")
                    
                    f.write("\nCONTACTS\n--------\n")
                    f.write("\nEmails:\n")
                    for email in sorted(self.results['emails']):
                        f.write(f"- {email}\n")
                    f.write("\nPhone Numbers:\n")
                    for phone in sorted(self.results['phones']):
                        f.write(f"- {phone}\n")
                    
                    f.write("\nTEXTS\n-----\n")
                    for text_item in self.results['texts']:
                        f.write(f"[{text_item['tag']}] {text_item['text']}\n")
                        f.write(f"   Source: {text_item['url']}\n\n")
                    
            elif export_format == "HTML":
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write("""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="utf-8">
                        <title>Web Scraper Results</title>
                        <style>
                            body { font-family: Arial, sans-serif; margin: 20px; }
                            h1, h2 { color: #333; }
                            .section { margin-bottom: 30px; }
                            table { border-collapse: collapse; width: 100%; }
                            th, td { text-align: left; padding: 8px; border: 1px solid #ddd; }
                            th { background-color: #f2f2f2; }
                            tr:nth-child(even) { background-color: #f9f9f9; }
                        </style>
                    </head>
                    <body>
                        <h1>Web Scraper Results</h1>
                        
                        <div class="section">
                            <h2>Summary</h2>
                            <p>Pages Visited: %s</p>
                            <p>Total Data Size: %s</p>
                        </div>
                    """ % (self.results['visited_pages'], self.format_size(self.results['total_data_size'])))
                    
                    # Links section
                    f.write("""
                        <div class="section">
                            <h2>Links (%s)</h2>
                            <table>
                                <tr>
                                    <th>URL</th>
                                    <th>Type</th>
                                </tr>
                    """ % len(self.results['links']))
                    
                    for link in sorted(self.results['internal_links']):
                        f.write(f'<tr><td><a href="{link}" target="_blank">{link}</a></td><td>Internal</td></tr>')
                    for link in sorted(self.results['external_links']):
                        f.write(f'<tr><td><a href="{link}" target="_blank">{link}</a></td><td>External</td></tr>')
                    
                    f.write('</table></div>')
                    
                    # Images section
                    f.write("""
                        <div class="section">
                            <h2>Images (%s)</h2>
                            <table>
                                <tr>
                                    <th>URL</th>
                                </tr>
                    """ % len(self.results['images']))
                    
                    for image in sorted(self.results['images']):
                        f.write(f'<tr><td><a href="{image}" target="_blank">{image}</a></td></tr>')
                    
                    f.write('</table></div>')
                    
                    # Contacts section
                    f.write("""
                        <div class="section">
                            <h2>Contacts</h2>
                            <h3>Emails (%s)</h3>
                            <ul>
                    """ % len(self.results['emails']))
                    
                    for email in sorted(self.results['emails']):
                        f.write(f'<li><a href="mailto:{email}">{email}</a></li>')
                    
                    f.write("""
                            </ul>
                            <h3>Phone Numbers (%s)</h3>
                            <ul>
                    """ % len(self.results['phones']))
                    
                    for phone in sorted(self.results['phones']):
                        f.write(f'<li>{phone}</li>')
                    
                    f.write('</ul></div>')
                    
                    # Texts section
                    f.write("""
                        <div class="section">
                            <h2>Texts (%s)</h2>
                            <table>
                                <tr>
                                    <th>Source</th>
                                    <th>Tag</th>
                                    <th>Text</th>
                                </tr>
                    """ % len(self.results['texts']))
                    
                    for text_item in self.results['texts']:
                        f.write(f'<tr><td>{text_item["url"]}</td><td>{text_item["tag"]}</td><td>{text_item["text"]}</td></tr>')
                    
                    f.write('</table></div>')
                    
                    f.write("""
                    </body>
                    </html>
                    """)
            
            QMessageBox.information(self, "Export Successful", 
                                  f"Results exported to {file_path}")
        
        except Exception as e:
            QMessageBox.critical(self, "Export Error", 
                               f"Failed to export results: {str(e)}")
    
    def closeEvent(self, event):
        """Handle application close event to clean up resources."""
        # Save window geometry and settings
        self.save_geometry()
        
        # Stop any running scraper thread
        if self.scraper_thread and self.scraper_thread.is_alive():
            self.scraper_thread.stop()
            # Give it a moment to clean up
            self.scraper_thread.join(1.0)
        
        # Accept the close event
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle("Fusion")
    
    # Enable high DPI scaling
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    # Create and show main window
    scraper = WebScraperApp()
    scraper.show()
    
    sys.exit(app.exec_())