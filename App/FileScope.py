"""
FileScope
A production-ready PyQt6 desktop application for file management and organization.

"""

from __future__ import annotations

import sys
import os
import platform
import mimetypes
import shutil
import sqlite3
import json
from pathlib import Path
import time
import hashlib
import re
import subprocess
from datetime import datetime
from typing import List, Dict, Set, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

# Media processing imports
try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

try:
    import face_recognition
    import numpy as np
    from io import BytesIO
    import base64
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False



from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QLineEdit, QPushButton, QTableView, QComboBox,
    QCheckBox, QProgressBar, QFileDialog, QMessageBox, QHeaderView,
    QGroupBox, QGridLayout, QTextEdit, QStatusBar, QFrame, QSpinBox,
    QScrollArea, QAbstractItemView, QTreeWidget, QTreeWidgetItem,
    QRadioButton, QButtonGroup, QTableWidget, QTableWidgetItem,QColorDialog,QSplitter
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QThread, pyqtSignal,
    QVariant, QTimer, QSize
)
from PyQt6.QtGui import QColor, QFont, QIcon

try:
    import face_recognition
    import numpy as np
    from multiprocessing import Pool, cpu_count, Manager
    from functools import partial
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False

def get_ffmpeg_path():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)

    return os.path.join(base_path, "tools", "ffmpeg", "ffmpeg.exe")

# ============================================================================
# DATA BASE
# ============================================================================

class DatabaseManager:
    """Local database for caching and preferences"""
    
    def __init__(self, db_path: str = "file_explorer.db"):
        self.db_path = db_path
        self.connection = None
        self.init_database()
    
    def init_database(self):
        """Initialize database and create tables"""
        self.connection = sqlite3.connect(self.db_path)
        cursor = self.connection.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_index (
                path TEXT PRIMARY KEY,
                name TEXT,
                extension TEXT,
                size INTEGER,
                modified REAL,
                is_dir INTEGER,
                last_checked REAL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS selected_extensions (
                extension TEXT PRIMARY KEY,
                selected INTEGER
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_extension ON file_index(extension)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_modified ON file_index(modified)
        """)
        
        self.connection.commit()
    
    def save_file_index(self, files: List[FileEntry]):
        """Batch save file index"""
        cursor = self.connection.cursor()
        current_time = time.time()
        
        data = [
            (f.path, f.name, f.extension, f.size, f.modified, 
             1 if f.is_dir else 0, current_time)
            for f in files
        ]
        
        cursor.executemany("""
            INSERT OR REPLACE INTO file_index 
            (path, name, extension, size, modified, is_dir, last_checked)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, data)
        
        self.connection.commit()
    
    def load_file_index(self) -> List[FileEntry]:
        """Load cached file index"""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT path, name, extension, size, modified, is_dir 
            FROM file_index
        """)
        
        files = []
        for row in cursor.fetchall():
            files.append(FileEntry(
                path=row[0],
                name=row[1],
                extension=row[2],
                size=row[3],
                modified=row[4],
                is_dir=bool(row[5])
            ))
        
        return files
    
    def get_indexed_paths(self) -> Set[str]:
        """Get set of all indexed file paths"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT path FROM file_index")
        return {row[0] for row in cursor.fetchall()}
    
    def remove_deleted_files(self, existing_paths: Set[str]):
        """Remove files from index that no longer exist"""
        cursor = self.connection.cursor()
        
        cursor.execute("SELECT path FROM file_index")
        indexed_paths = {row[0] for row in cursor.fetchall()}
        
        deleted_paths = indexed_paths - existing_paths
        
        if deleted_paths:
            cursor.executemany(
                "DELETE FROM file_index WHERE path = ?",
                [(p,) for p in deleted_paths]
            )
            self.connection.commit()
    
    def get_file_info(self, path: str) -> Optional[dict]:
        """Get cached file info"""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT size, modified FROM file_index WHERE path = ?
        """, (path,))
        
        row = cursor.fetchone()
        if row:
            return {'size': row[0], 'modified': row[1]}
        return None
    
    def save_preference(self, key: str, value: any):
        """Save user preference"""
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO user_preferences (key, value)
            VALUES (?, ?)
        """, (key, json.dumps(value)))
        self.connection.commit()
    
    def get_preference(self, key: str, default: any = None) -> any:
        """Get user preference"""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT value FROM user_preferences WHERE key = ?
        """, (key,))
        
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row[0])
            except:
                return default
        return default
    
    def save_selected_extensions(self, extensions: Set[str]):
        """Save selected extensions"""
        cursor = self.connection.cursor()
        
        cursor.execute("DELETE FROM selected_extensions")
        
        if extensions:
            cursor.executemany("""
                INSERT INTO selected_extensions (extension, selected)
                VALUES (?, 1)
            """, [(ext,) for ext in extensions])
        
        self.connection.commit()
    
    def load_selected_extensions(self) -> Set[str]:
        """Load selected extensions"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT extension FROM selected_extensions WHERE selected = 1")
        return {row[0] for row in cursor.fetchall()}
    
    def get_index_stats(self) -> dict:
        """Get indexing statistics"""
        cursor = self.connection.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM file_index")
        total_files = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT extension) FROM file_index")
        total_extensions = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(size) FROM file_index")
        total_size = cursor.fetchone()[0] or 0
        
        return {
            'total_files': total_files,
            'total_extensions': total_extensions,
            'total_size': total_size
        }
    
    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()

# ============================================================================
# TRASLATION
# ============================================================================

class TranslationManager:
    """Manages application translations"""
    
    TRANSLATIONS = {
        'en': {
            'app_title': 'FileScope',
            'file_explorer': 'File Explorer',
            'file_organizer': 'File Organizer',
            'duplicate_finder': 'Duplicate Finder',
            'media_converter': 'Media Converter',
            'face_search': 'Face Search',
            'help': 'Help',
            'settings': 'Settings',
            'search': 'Search',
            'browse': 'Browse',
            'start': 'Start',
            'cancel': 'Cancel',
            'delete': 'Delete',
            'clear': 'Clear',
            'ready': 'Ready',
            'select_folder': 'Select Folder',
            'no_matches': 'No matches found',
            'search_complete': 'Search complete',
            'language': 'Language',
            'theme': 'Theme',
            'font': 'Font',
            'font_size': 'Font Size',
            'background_processing': 'Background Processing',
            'apply_settings': 'Apply Settings',
            'reset_defaults': 'Reset to Defaults',
            'light_theme': 'Light',
            'dark_theme': 'Dark',
            'blue_theme': 'Blue',
            'reference_image': 'Reference Image',
            'search_folder': 'Search Folder',
            'similarity_threshold': 'Similarity Threshold',
            'processing_speed': 'Processing Speed',
            'results': 'Results',
            'copy_to_folders': 'Copy Matches to Folders',
            'select_output': 'Select Output Directory',
            'organizing_results': 'Organizing results into folders...',
            'all_together': 'All Together',
        },
        'fa': {
            'app_title': 'FileScope',
            'file_explorer': 'مرورگر فایل',
            'file_organizer': 'سازماندهی فایل',
            'duplicate_finder': 'یافتن تکراری‌ها',
            'media_converter': 'تبدیل رسانه',
            'face_search': 'جستجوی چهره',
            'help': 'راهنما',
            'settings': 'تنظیمات',
            'search': 'جستجو',
            'browse': 'انتخاب',
            'start': 'شروع',
            'cancel': 'لغو',
            'delete': 'حذف',
            'clear': 'پاک کردن',
            'ready': 'آماده',
            'select_folder': 'انتخاب پوشه',
            'no_matches': 'موردی یافت نشد',
            'search_complete': 'جستجو کامل شد',
            'language': 'زبان',
            'theme': 'تم',
            'font': 'فونت',
            'font_size': 'اندازه فونت',
            'background_processing': 'پردازش پس‌زمینه',
            'apply_settings': 'اعمال تنظیمات',
            'reset_defaults': 'بازگشت به پیش‌فرض',
            'light_theme': 'روشن',
            'dark_theme': 'تیره',
            'blue_theme': 'آبی',
            'reference_image': 'تصویر مرجع',
            'search_folder': 'پوشه جستجو',
            'similarity_threshold': 'آستانه شباهت',
            'processing_speed': 'سرعت پردازش',
            'results': 'نتایج',
            'copy_to_folders': 'کپی به پوشه‌ها',
            'select_output': 'انتخاب مسیر خروجی',
            'organizing_results': 'سازماندهی نتایج در پوشه‌ها...',
            'all_together': 'همه با هم',
        }
    }
    
    def __init__(self):
        self.current_language = 'en'
    
    def set_language(self, lang_code: str):
        if lang_code in self.TRANSLATIONS:
            self.current_language = lang_code
    
    def get(self, key: str) -> str:
        return self.TRANSLATIONS.get(self.current_language, {}).get(key, key)

# ============================================================================
# THEME
# ============================================================================

class ThemeManager:
    """Manages application themes"""
    
    THEMES = {
        'light': {
            'background': '#f4f6fb',      # light neutral background
            'foreground': '#1f2937',      # dark gray text (better than pure black)
            'button_bg': '#6366f1',       # indigo
            'button_fg': '#ffffff',
            'button_hover': '#4f46e5',    # darker indigo
            'input_border': '#c7cbd6',
            'group_border': '#d1d5db',
            'selection': '#818cf8',       # soft indigo highlight
        },

        'dark': {
            'background': '#1f2933',      # modern dark gray (not pure black)
            'foreground': '#f9fafb',      # near-white text
            'button_bg': '#3b82f6',       # blue
            'button_fg': '#ffffff',
            'button_hover': '#2563eb',    # darker blue
            'input_border': '#374151',
            'group_border': '#4b5563',
            'selection': '#60a5fa',       # light blue highlight
        },

        'blue': {
            'background': '#0f172a',      # deep navy
            'foreground': '#e5e7eb',      # soft light text
            'button_bg': '#38bdf8',       # cyan-blue
            'button_fg': '#0f172a',       # dark text on bright button
            'button_hover': '#0ea5e9',    # stronger cyan
            'input_border': '#334155',
            'group_border': '#475569',
            'selection': '#38bdf8',
        },
        'custom': {
            'background': '#ffffff',
            'foreground': '#000000',
            'button_bg': '#3498db',
            'button_fg': '#ffffff',
            'button_hover': '#2980b9',
            'input_border': '#bdc3c7',
            'group_border': '#cccccc',
            'selection': '#3498db',
        }
    }

    
    def __init__(self):
        self.current_theme = 'light'
        self.custom_colors = self.THEMES['custom'].copy()
    
    def set_theme(self, theme_name: str):
        if theme_name in self.THEMES:
            self.current_theme = theme_name
    
    def set_custom_color(self, element: str, color: str):
        """Set a custom theme color"""
        if element in self.custom_colors:
            self.custom_colors[element] = color
    
    def get_custom_colors(self) -> dict:
        """Get custom theme colors"""
        return self.custom_colors.copy()
    
    def load_custom_colors(self, colors: dict):
        """Load custom theme colors"""
        for key, value in colors.items():
            if key in self.custom_colors:
                self.custom_colors[key] = value
    
    def get_stylesheet(self) -> str:
        if self.current_theme == 'custom':
            theme = self.custom_colors
        else:
            theme = self.THEMES.get(self.current_theme, self.THEMES['light'])
        
        return f"""
            QWidget {{
                background-color: {theme['background']};
                color: {theme['foreground']};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {theme['group_border']};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                color: {theme['foreground']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
            QPushButton {{
                padding: 5px 15px;
                border-radius: 3px;
                background-color: {theme['button_bg']};
                color: {theme['button_fg']};
                border: none;
            }}
            QPushButton:hover {{
                background-color: {theme['button_hover']};
            }}
            QPushButton:pressed {{
                background-color: {theme['button_hover']};
            }}
            QPushButton:disabled {{
                background-color: #95a5a6;
            }}
            QLineEdit, QTextEdit, QSpinBox, QComboBox {{
                padding: 5px;
                border: 1px solid {theme['input_border']};
                border-radius: 3px;
                background-color: {theme['background']};
                color: {theme['foreground']};
            }}
            QTableView, QTreeWidget, QTableWidget {{
                border: 1px solid {theme['input_border']};
                border-radius: 3px;
                selection-background-color: {theme['selection']};
                background-color: {theme['background']};
                color: {theme['foreground']};
            }}
            QLabel {{
                color: {theme['foreground']};
            }}
            QCheckBox, QRadioButton {{
                color: {theme['foreground']};
            }}
            QTabWidget::pane {{
                border: 1px solid {theme['group_border']};
                background-color: {theme['background']};
            }}
            QTabBar::tab {{
                background-color: {theme['background']};
                color: {theme['foreground']};
                border: 1px solid {theme['group_border']};
                padding: 8px 16px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {theme['button_bg']};
                color: {theme['button_fg']};
            }}
            QProgressBar {{
                border: 1px solid {theme['input_border']};
                border-radius: 3px;
                text-align: center;
                color: {theme['foreground']};
            }}
            QProgressBar::chunk {{
                background-color: {theme['button_bg']};
            }}
        """

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class FileEntry:
    """Represents a file with metadata"""
    name: str
    path: str
    extension: str
    size: int
    modified: float
    is_dir: bool = False

    def size_formatted(self) -> str:
        """Format size in human-readable format"""
        size = self.size
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    def modified_formatted(self) -> str:
        """Format modification time"""
        try:
            dt = datetime.fromtimestamp(self.modified)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            return "Unknown"


@dataclass
class DuplicateFile:
    """Represents a single file in a duplicate group"""
    name: str
    path: str
    size: int
    modified: float
    hash: Optional[str] = None  # For deep scan mode
    
    def size_formatted(self) -> str:
        """Format file size in human-readable format"""
        size = self.size
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"
    
    def modified_formatted(self) -> str:
        """Format modification time"""
        try:
            dt = datetime.fromtimestamp(self.modified)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            return "Unknown"
    
    def __hash__(self):
        return hash(self.path)
    
    def __eq__(self, other):
        if isinstance(other, DuplicateFile):
            return self.path == other.path
        return False


@dataclass
class DuplicateGroup:
    """Represents a group of duplicate files"""
    key: str  # Normalized name or hash
    files: List[DuplicateFile] = field(default_factory=list)
    
    def total_size(self) -> int:
        """Calculate total size of all files in group"""
        return sum(f.size for f in self.files)
    
    def count(self) -> int:
        """Get number of files in group"""
        return len(self.files)
    
    def wasted_space(self) -> int:
        """Calculate wasted space (all files except one)"""
        if len(self.files) <= 1:
            return 0
        # Keep the largest file, count others as wasted
        sizes = sorted([f.size for f in self.files], reverse=True)
        return sum(sizes[1:])


@dataclass
class MediaFile:
    """Represents a media file for conversion"""
    source_path: str
    source_format: str
    target_format: str
    status: str = "Pending"
    error_message: str = ""
    output_path: str = ""
    
    @property
    def filename(self) -> str:
        """Get the filename from source path"""
        return os.path.basename(self.source_path)
    
    @property
    def output_filename(self) -> str:
        """Get the expected output filename"""
        if self.output_path:
            return os.path.basename(self.output_path)
        name = os.path.splitext(self.filename)[0]
        return f"{name}.{self.target_format}"
    
    
@dataclass
class FaceMatch:
    """Represents a matched face in an image"""
    image_path: str
    similarity: float
    face_locations: List[Tuple[int, int, int, int]] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    matched_face_ids: List[int] = field(default_factory=list)
    
    @property
    def filename(self) -> str:
        return os.path.basename(self.image_path)
    
    @property
    def similarity_percent(self) -> str:
        return f"{self.similarity * 100:.1f}%"
    
    def __lt__(self, other):
        return self.similarity > other.similarity

@dataclass
class DetectedFace:
    """Represents a detected face in the reference image"""
    face_id: int
    encoding: np.ndarray
    location: Tuple[int, int, int, int]  # top, right, bottom, left
    thumbnail: Optional[np.ndarray] = None
    selected: bool = False
    
    def get_thumbnail_base64(self) -> Optional[str]:
        """Convert thumbnail to base64 for display in Qt"""
        if self.thumbnail is None:
            return None
        
        try:
            from PIL import Image as PILImage
            pil_image = PILImage.fromarray(self.thumbnail)
            buffer = BytesIO()
            pil_image.save(buffer, format='PNG')
            img_str = base64.b64encode(buffer.getvalue()).decode()
            return f"data:image/png;base64,{img_str}"
        except:
            return None
    
    def get_location_string(self) -> str:
        """Get human-readable location"""
        top, right, bottom, left = self.location
        width = right - left
        height = bottom - top
        return f"Position: ({left}, {top}) | Size: {width}x{height}px"


# FILE / CLASS TO UPDATE: process_single_image_fast function
# REPLACE WITH THE FOLLOWING CODE:

# ============================================================================
# TABLE MODEL FOR FILE DISPLAY
# ============================================================================

class FileTableModel(QAbstractTableModel):
    """Custom table model for displaying file entries"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: List[FileEntry] = []
        self.headers = ['Name', 'Extension', 'Size', 'Full Path', 'Last Modified']
        self.sort_column = 0
        self.sort_order = Qt.SortOrder.AscendingOrder

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.files)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.headers)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.files)):
            return QVariant()

        file = self.files[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return file.name
            elif col == 1:
                return file.extension
            elif col == 2:
                return file.size_formatted()
            elif col == 3:
                return file.path
            elif col == 4:
                return file.modified_formatted()

        elif role == Qt.ItemDataRole.ToolTipRole:
            return file.path

        elif role == Qt.ItemDataRole.ForegroundRole:
            if file.is_dir:
                return QColor(0, 100, 200)

        return QVariant()

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return QVariant()

    def sort(self, column: int, order: Qt.SortOrder):
        """Sort data by column"""
        self.layoutAboutToBeChanged.emit()
        self.sort_column = column
        self.sort_order = order

        reverse = (order == Qt.SortOrder.DescendingOrder)

        if column == 0:
            self.files.sort(key=lambda f: f.name.lower(), reverse=reverse)
        elif column == 1:
            self.files.sort(key=lambda f: f.extension.lower(), reverse=reverse)
        elif column == 2:
            self.files.sort(key=lambda f: f.size, reverse=reverse)
        elif column == 3:
            self.files.sort(key=lambda f: f.path.lower(), reverse=reverse)
        elif column == 4:
            self.files.sort(key=lambda f: f.modified, reverse=reverse)

        self.layoutChanged.emit()

    def set_files(self, files: List[FileEntry]):
        """Update the model with new files"""
        self.beginResetModel()
        self.files = files
        self.endResetModel()

    def get_file(self, row: int) -> Optional[FileEntry]:
        """Get file at specified row"""
        if 0 <= row < len(self.files):
            return self.files[row]
        return None


# ============================================================================
# SYSTEM-WIDE FILE INDEXER (BACKGROUND)
# ============================================================================

class SystemIndexerThread(QThread):
    """Background thread for incremental system indexing"""

    progress = pyqtSignal(str)
    files_indexed = pyqtSignal(int)
    indexing_complete = pyqtSignal(int)
    error_occurred = pyqtSignal(str)

    def __init__(self, db_path: str, force_full_index: bool = False, parent=None):
        super().__init__(parent)
        self.db_path = db_path  # Store path, not connection
        self.force_full_index = force_full_index
        self.should_stop = False
        self.index: List[FileEntry] = []
        self.db_manager = None  # Will be created in run()

    def run(self):
        """Index all available drives with incremental updates"""
        try:
            # Create thread-local database connection
            self.db_manager = DatabaseManager(self.db_path)
            
            self.index.clear()
            
            if not self.force_full_index:
                self.progress.emit("Loading cached index...")
                cached_files = self.db_manager.load_file_index()
                
                if len(cached_files) > 1000:
                    self.progress.emit(f"Loaded {len(cached_files):,} files from cache")
                    self.index = cached_files
                    self.files_indexed.emit(len(cached_files))
                    
                    # Use QTimer for delayed call since we're in a thread
                    QTimer.singleShot(500, lambda: self.incremental_update())
                    return
            
            self.full_index()
            
        except Exception as e:
            self.error_occurred.emit(f"Database error: {str(e)}")
        finally:
            # Clean up thread-local connection
            if self.db_manager:
                self.db_manager.close()
                self.db_manager = None
    
    def full_index(self):
        """Perform full system indexing"""
        total_files = 0
        drives = self.get_available_drives()
        
        batch = []
        batch_size = 500
        
        for drive in drives:
            if self.should_stop:
                break

            self.progress.emit(f"Indexing {drive}...")

            try:
                for root, dirs, files in os.walk(drive):
                    if self.should_stop:
                        break

                    dirs[:] = [d for d in dirs if not d.startswith('.') and 
                              d not in ['$RECYCLE.BIN', 'System Volume Information', 
                                       'Windows', 'ProgramData', 'Program Files', 
                                       'Program Files (x86)', 'AppData', 
                                       'node_modules', '.git', '__pycache__']]

                    for filename in files:
                        if self.should_stop:
                            break

                        try:
                            filepath = os.path.join(root, filename)
                            stat = os.stat(filepath)
                            ext = os.path.splitext(filename)[1].lower()

                            entry = FileEntry(
                                name=filename,
                                path=filepath,
                                extension=ext,
                                size=stat.st_size,
                                modified=stat.st_mtime,
                                is_dir=False
                            )

                            self.index.append(entry)
                            batch.append(entry)
                            total_files += 1

                            if len(batch) >= batch_size:
                                self.db_manager.save_file_index(batch)
                                batch.clear()
                                self.files_indexed.emit(total_files)

                        except (PermissionError, OSError, FileNotFoundError):
                            continue

            except Exception as e:
                self.error_occurred.emit(f"Error indexing {drive}: {str(e)}")

        if batch:
            self.db_manager.save_file_index(batch)
        
        if not self.should_stop:
            self.indexing_complete.emit(total_files)
    
    def incremental_update(self):
        """Perform incremental update of cached index"""
        if not self.db_manager:
            return
            
        self.progress.emit("Checking for changes...")
        
        indexed_paths = self.db_manager.get_indexed_paths()
        existing_paths = set()
        new_files = []
        updated_files = []
        checked = 0
        
        drives = self.get_available_drives()
        
        for drive in drives:
            if self.should_stop:
                break
            
            try:
                for root, dirs, files in os.walk(drive):
                    if self.should_stop:
                        break
                    
                    dirs[:] = [d for d in dirs if not d.startswith('.') and 
                              d not in ['$RECYCLE.BIN', 'System Volume Information', 
                                       'Windows', 'ProgramData', 'Program Files', 
                                       'Program Files (x86)', 'AppData',
                                       'node_modules', '.git', '__pycache__']]
                    
                    for filename in files:
                        if self.should_stop:
                            break
                        
                        try:
                            filepath = os.path.join(root, filename)
                            existing_paths.add(filepath)
                            
                            stat = os.stat(filepath)
                            
                            if filepath not in indexed_paths:
                                ext = os.path.splitext(filename)[1].lower()
                                entry = FileEntry(
                                    name=filename,
                                    path=filepath,
                                    extension=ext,
                                    size=stat.st_size,
                                    modified=stat.st_mtime,
                                    is_dir=False
                                )
                                new_files.append(entry)
                                self.index.append(entry)
                            else:
                                cached_info = self.db_manager.get_file_info(filepath)
                                if cached_info:
                                    if (stat.st_size != cached_info['size'] or 
                                        abs(stat.st_mtime - cached_info['modified']) > 1):
                                        
                                        ext = os.path.splitext(filename)[1].lower()
                                        entry = FileEntry(
                                            name=filename,
                                            path=filepath,
                                            extension=ext,
                                            size=stat.st_size,
                                            modified=stat.st_mtime,
                                            is_dir=False
                                        )
                                        updated_files.append(entry)
                                        
                                        for i, f in enumerate(self.index):
                                            if f.path == filepath:
                                                self.index[i] = entry
                                                break
                            
                            checked += 1
                            if checked % 1000 == 0:
                                self.progress.emit(f"Checked {checked:,} files...")
                        
                        except (PermissionError, OSError, FileNotFoundError):
                            continue
            
            except Exception:
                continue
        
        if new_files:
            self.db_manager.save_file_index(new_files)
            self.progress.emit(f"Added {len(new_files):,} new files")
        
        if updated_files:
            self.db_manager.save_file_index(updated_files)
            self.progress.emit(f"Updated {len(updated_files):,} files")
        
        self.db_manager.remove_deleted_files(existing_paths)
        
        if not self.should_stop:
            total = len(self.index)
            self.indexing_complete.emit(total)

    def get_available_drives(self) -> List[str]:
        """Get list of available drives based on platform"""
        drives = []
        
        if sys.platform == 'win32':
            import string
            for letter in string.ascii_uppercase:
                drive = f'{letter}:\\'
                if os.path.exists(drive):
                    drives.append(drive)
        else:
            drives.append('/')
        
        return drives

    def stop(self):
        """Stop indexing"""
        self.should_stop = True

# ============================================================================
# FILE SEARCH THREAD
# ============================================================================

class FileSearchThread(QThread):
    """Background thread for searching indexed files"""

    results_ready = pyqtSignal(list)
    search_complete = pyqtSignal(int)

    def __init__(self, index: List[FileEntry], query: str, folder_filter: str = "", parent=None):
        super().__init__(parent)
        self.index = index
        self.query = query.lower()
        self.folder_filter = folder_filter.lower()
        self.should_stop = False

    def run(self):
        """Search through the index"""
        results = []

        for entry in self.index:
            if self.should_stop:
                break

            if self.query in entry.name.lower():
                if self.folder_filter == "" or self.folder_filter in entry.path.lower():
                    results.append(entry)

                    if len(results) % 100 == 0:
                        self.results_ready.emit(results.copy())
                        results.clear()

        if results and not self.should_stop:
            self.results_ready.emit(results)

        if not self.should_stop:
            total = len([r for r in self.index if self.query in r.name.lower()])
            self.search_complete.emit(total)

    def stop(self):
        """Stop the search"""
        self.should_stop = True


# ============================================================================
# DUPLICATE SCANNER (FAST MODE)
# ============================================================================

class FastDuplicateScannerThread(QThread):
    """Fast duplicate detection using filename normalization"""

    progress = pyqtSignal(str)
    files_scanned = pyqtSignal(int)
    scan_complete = pyqtSignal(dict)  # Dict[str, List[DuplicateFile]]
    error_occurred = pyqtSignal(str)

    def __init__(self, root_folder: str, use_size_filter: bool = False, parent=None):
        super().__init__(parent)
        self.root_folder = root_folder
        self.use_size_filter = use_size_filter
        self.should_stop = False
        self.case_sensitive = sys.platform != 'win32'

    def run(self):
        """Scan folder for duplicate file names"""
        try:
            # Step 1: Collect all files
            file_map: Dict[Tuple, List[DuplicateFile]] = defaultdict(list)
            total_scanned = 0

            self.progress.emit("Scanning files...")

            for root, dirs, files in os.walk(self.root_folder):
                if self.should_stop:
                    return

                dirs[:] = [d for d in dirs if not d.startswith('.')]

                for filename in files:
                    if self.should_stop:
                        return

                    try:
                        filepath = os.path.join(root, filename)
                        stat = os.stat(filepath)

                        # Normalize filename (remove copy patterns)
                        normalized = self.normalize_filename(filename)

                        file_entry = DuplicateFile(
                            name=filename,
                            path=filepath,
                            size=stat.st_size,
                            modified=stat.st_mtime
                        )

                        # Create key: normalized name + optional size
                        if self.use_size_filter:
                            key = (normalized, stat.st_size)
                        else:
                            key = (normalized,)

                        file_map[key].append(file_entry)

                        total_scanned += 1
                        if total_scanned % 100 == 0:
                            self.files_scanned.emit(total_scanned)

                    except (PermissionError, OSError):
                        continue

            # Step 2: Filter duplicates
            duplicates = {}
            for key, files in file_map.items():
                if len(files) > 1:
                    # Use first part of key as display name
                    display_key = key[0] if isinstance(key, tuple) else key
                    duplicates[display_key] = files

            if not self.should_stop:
                self.files_scanned.emit(total_scanned)
                self.scan_complete.emit(duplicates)

        except Exception as e:
            self.error_occurred.emit(f"Scan error: {str(e)}")

    def normalize_filename(self, filename: str) -> str:
        """
        Normalize filename by removing common copy patterns.
        Examples:
        - "file (1).txt" -> "file.txt"
        - "file - Copy.txt" -> "file.txt"
        - "file_copy.txt" -> "file.txt"
        """
        # Remove extension temporarily
        name, ext = os.path.splitext(filename)

        # Remove common copy patterns
        patterns = [
            r'\s*\(\d+\)$',  # " (1)", " (2)", etc.
            r'\s*-\s*Copy$',  # " - Copy"
            r'\s*Copy$',  # " Copy"
            r'_copy$',  # "_copy"
            r'\s*-\s*\d+$',  # " - 1", " - 2"
            r'_\d+$',  # "_1", "_2"
        ]

        for pattern in patterns:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Handle case sensitivity
        if not self.case_sensitive:
            name = name.lower()
            ext = ext.lower()

        return name + ext

    def stop(self):
        """Stop scanning"""
        self.should_stop = True


# ============================================================================
# DUPLICATE SCANNER (DEEP MODE)
# ============================================================================

class DeepDuplicateScannerThread(QThread):
    """Deep duplicate detection using content hashing"""

    progress = pyqtSignal(str)
    files_scanned = pyqtSignal(int)
    scan_complete = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, root_folder: str, hash_algorithm: str = 'md5', parent=None):
        super().__init__(parent)
        self.root_folder = root_folder
        self.hash_algorithm = hash_algorithm
        self.should_stop = False

    def run(self):
        """Scan folder and hash files"""
        try:
            # Step 1: Group by size first (optimization)
            size_map: Dict[int, List[str]] = defaultdict(list)
            total_scanned = 0

            self.progress.emit("Scanning files by size...")

            for root, dirs, files in os.walk(self.root_folder):
                if self.should_stop:
                    return

                dirs[:] = [d for d in dirs if not d.startswith('.')]

                for filename in files:
                    if self.should_stop:
                        return

                    try:
                        filepath = os.path.join(root, filename)
                        stat = os.stat(filepath)

                        # Only consider files > 0 bytes
                        if stat.st_size > 0:
                            size_map[stat.st_size].append(filepath)

                        total_scanned += 1
                        if total_scanned % 100 == 0:
                            self.files_scanned.emit(total_scanned)

                    except (PermissionError, OSError):
                        continue

            # Step 2: Hash files with matching sizes
            hash_map: Dict[str, List[DuplicateFile]] = defaultdict(list)
            files_to_hash = [path for paths in size_map.values() if len(paths) > 1 for path in paths]
            hashed_count = 0

            self.progress.emit(f"Hashing {len(files_to_hash)} potential duplicates...")

            for filepath in files_to_hash:
                if self.should_stop:
                    return

                try:
                    file_hash = self.hash_file(filepath)
                    stat = os.stat(filepath)

                    file_entry = DuplicateFile(
                        name=os.path.basename(filepath),
                        path=filepath,
                        size=stat.st_size,
                        modified=stat.st_mtime,
                        hash=file_hash
                    )

                    hash_map[file_hash].append(file_entry)

                    hashed_count += 1
                    if hashed_count % 10 == 0:
                        self.progress.emit(f"Hashed {hashed_count}/{len(files_to_hash)} files...")

                except Exception as e:
                    continue

            # Step 3: Filter duplicates
            duplicates = {
                hash_val: files for hash_val, files in hash_map.items()
                if len(files) > 1
            }

            if not self.should_stop:
                self.scan_complete.emit(duplicates)

        except Exception as e:
            self.error_occurred.emit(f"Deep scan error: {str(e)}")

    def hash_file(self, filepath: str) -> str:
        """Calculate file hash"""
        if self.hash_algorithm == 'md5':
            hasher = hashlib.md5()
        elif self.hash_algorithm == 'sha1':
            hasher = hashlib.sha1()
        else:  # sha256
            hasher = hashlib.sha256()

        with open(filepath, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)

        return hasher.hexdigest()

    def stop(self):
        """Stop scanning"""
        self.should_stop = True


# ============================================================================
# FILE DELETION THREAD
# ============================================================================

class FileDeletionThread(QThread):
    """Background thread for deleting files"""

    progress = pyqtSignal(int, int)
    status_message = pyqtSignal(str)
    deletion_complete = pyqtSignal(int, int)
    error_occurred = pyqtSignal(str)

    def __init__(self, files_to_delete: List[DuplicateFile], parent=None):
        super().__init__(parent)
        self.files_to_delete = files_to_delete
        self.should_stop = False

    def run(self):
        """Delete files"""
        deleted_count = 0
        failed_count = 0
        total = len(self.files_to_delete)

        for i, file in enumerate(self.files_to_delete):
            if self.should_stop:
                break

            try:
                if not os.path.exists(file.path):
                    failed_count += 1
                    continue

                if not os.access(file.path, os.W_OK):
                    failed_count += 1
                    continue

                os.remove(file.path)
                deleted_count += 1

            except Exception:
                failed_count += 1

            self.progress.emit(i + 1, total)

        if not self.should_stop:
            self.deletion_complete.emit(deleted_count, failed_count)

    def stop(self):
        """Stop deletion"""
        self.should_stop = True


# ============================================================================
# MEDIA CONVERSION UTILITIES
# ============================================================================

class MediaConverter:
    """Utility class for media conversion operations"""
    
    @staticmethod
    def check_ffmpeg() -> bool:
        """Check if ffmpeg is available"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-version'],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False
    
    @staticmethod
    def convert_image(source: str, target: str, quality: int = 85) -> bool:
        """
        Convert image using Pillow
        
        Args:
            source: Source file path
            target: Target file path
            quality: Quality (1-100) for lossy formats
            
        Returns:
            True if successful, False otherwise
        """
        if not PILLOW_AVAILABLE:
            raise RuntimeError("Pillow is not installed")
        
        try:
            with Image.open(source) as img:
                # Convert RGBA to RGB if saving as JPEG
                if target.lower().endswith('.jpg') or target.lower().endswith('.jpeg'):
                    if img.mode in ('RGBA', 'LA', 'P'):
                        rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        rgb_img.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                        img = rgb_img
                
                # Save with appropriate settings
                if target.lower().endswith(('.jpg', '.jpeg')):
                    img.save(target, 'JPEG', quality=quality, optimize=True)
                elif target.lower().endswith('.png'):
                    img.save(target, 'PNG', optimize=True)
                elif target.lower().endswith('.webp'):
                    img.save(target, 'WEBP', quality=quality)
                else:
                    img.save(target)
            
            return True
        except Exception as e:
            raise RuntimeError(f"Image conversion failed: {str(e)}")
    
    @staticmethod
    def convert_video(source: str, target: str, quality: str = 'medium', 
                     resolution: Optional[str] = None) -> bool:
        """
        Convert video using ffmpeg
        
        Args:
            source: Source file path
            target: Target file path
            quality: Quality preset (fast, medium, slow)
            resolution: Optional resolution (e.g., '1920x1080')
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cmd = ['ffmpeg', '-i', source, '-y']
            
            # Quality preset
            if quality == 'fast':
                cmd.extend(['-preset', 'fast', '-crf', '28'])
            elif quality == 'slow':
                cmd.extend(['-preset', 'slow', '-crf', '18'])
            else:  # medium
                cmd.extend(['-preset', 'medium', '-crf', '23'])
            
            # Resolution
            if resolution:
                cmd.extend(['-s', resolution])
            
            # Codec selection based on output format
            if target.lower().endswith('.mp4'):
                cmd.extend(['-c:v', 'libx264', '-c:a', 'aac'])
            elif target.lower().endswith('.webm'):
                cmd.extend(['-c:v', 'libvpx-vp9', '-c:a', 'libopus'])
            elif target.lower().endswith('.mkv'):
                cmd.extend(['-c:v', 'libx264', '-c:a', 'aac'])
            
            cmd.append(target)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300  # 5 minute timeout
            )
            
            return result.returncode == 0
        except Exception as e:
            raise RuntimeError(f"Video conversion failed: {str(e)}")
    
    @staticmethod
    def convert_audio(source: str, target: str, bitrate: str = '192k',
                     sample_rate: Optional[int] = None) -> bool:
        """
        Convert audio using ffmpeg
        
        Args:
            source: Source file path
            target: Target file path
            bitrate: Bitrate (e.g., '128k', '192k', '320k')
            sample_rate: Optional sample rate in Hz (e.g., 44100, 48000)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            cmd = ['ffmpeg', '-i', source, '-y']
            
            # Codec selection
            if target.lower().endswith('.mp3'):
                cmd.extend(['-c:a', 'libmp3lame', '-b:a', bitrate])
            elif target.lower().endswith('.aac'):
                cmd.extend(['-c:a', 'aac', '-b:a', bitrate])
            elif target.lower().endswith('.flac'):
                cmd.extend(['-c:a', 'flac'])
            elif target.lower().endswith('.wav'):
                cmd.extend(['-c:a', 'pcm_s16le'])
            
            # Sample rate
            if sample_rate:
                cmd.extend(['-ar', str(sample_rate)])
            
            cmd.append(target)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=180  # 3 minute timeout
            )
            
            return result.returncode == 0
        except Exception as e:
            raise RuntimeError(f"Audio conversion failed: {str(e)}")


# ============================================================================
# MEDIA CONVERSION THREAD
# ============================================================================

class MediaConversionThread(QThread):
    """Background thread for media file conversion"""
    
    progress = pyqtSignal(int, int)  # current, total
    file_started = pyqtSignal(str)  # filename
    file_completed = pyqtSignal(str, bool, str)  # filename, success, message
    conversion_complete = pyqtSignal(int, int, int)  # success, failed, skipped
    
    def __init__(self, files: List[MediaFile], output_dir: str, 
                 image_quality: int = 85,
                 video_quality: str = 'medium',
                 video_resolution: Optional[str] = None,
                 audio_bitrate: str = '192k',
                 audio_sample_rate: Optional[int] = None,
                 delete_originals: bool = False,
                 parent=None):
        super().__init__(parent)
        self.files = files
        self.output_dir = output_dir
        self.image_quality = image_quality
        self.video_quality = video_quality
        self.video_resolution = video_resolution
        self.audio_bitrate = audio_bitrate
        self.audio_sample_rate = audio_sample_rate
        self.delete_originals = delete_originals
        self.should_stop = False
    
    def run(self):
        """Convert all files"""
        success_count = 0
        failed_count = 0
        skipped_count = 0
        total = len(self.files)
        
        for i, media_file in enumerate(self.files):
            if self.should_stop:
                break
            
            self.file_started.emit(media_file.filename)
            
            try:
                # Generate output path
                output_filename = f"{os.path.splitext(media_file.filename)[0]}.{media_file.target_format}"
                output_path = os.path.join(self.output_dir, output_filename)
                
                # Handle name conflicts
                counter = 1
                base_name = os.path.splitext(output_filename)[0]
                ext = media_file.target_format
                while os.path.exists(output_path):
                    output_filename = f"{base_name}_{counter}.{ext}"
                    output_path = os.path.join(self.output_dir, output_filename)
                    counter += 1
                
                media_file.output_path = output_path
                
                # Determine media type
                source_ext = media_file.source_format.lower()
                
                # Perform conversion
                if source_ext in ['jpg', 'jpeg', 'png', 'bmp', 'webp', 'tiff']:
                    # Image conversion
                    if not PILLOW_AVAILABLE:
                        raise RuntimeError("Pillow not installed")
                    
                    MediaConverter.convert_image(
                        media_file.source_path,
                        output_path,
                        self.image_quality
                    )
                
                elif source_ext in ['mp4', 'mkv', 'avi', 'mov', 'webm']:
                    # Video conversion
                    if not MediaConverter.check_ffmpeg():
                        raise RuntimeError("ffmpeg not found")
                    
                    MediaConverter.convert_video(
                        media_file.source_path,
                        output_path,
                        self.video_quality,
                        self.video_resolution
                    )
                
                elif source_ext in ['mp3', 'wav', 'aac', 'flac', 'ogg', 'm4a']:
                    # Audio conversion
                    if not MediaConverter.check_ffmpeg():
                        raise RuntimeError("ffmpeg not found")
                    
                    MediaConverter.convert_audio(
                        media_file.source_path,
                        output_path,
                        self.audio_bitrate,
                        self.audio_sample_rate
                    )
                
                else:
                    raise RuntimeError(f"Unsupported format: {source_ext}")
                
                # Verify output exists
                if not os.path.exists(output_path):
                    raise RuntimeError("Output file was not created")
                
                # Delete original if requested
                if self.delete_originals:
                    try:
                        os.remove(media_file.source_path)
                    except:
                        pass  # Don't fail if we can't delete original
                
                success_count += 1
                self.file_completed.emit(media_file.filename, True, "Success")
            
            except Exception as e:
                failed_count += 1
                error_msg = str(e)
                self.file_completed.emit(media_file.filename, False, error_msg)
            
            self.progress.emit(i + 1, total)
        
        if not self.should_stop:
            self.conversion_complete.emit(success_count, failed_count, skipped_count)
    
    def stop(self):
        """Stop conversion"""
        self.should_stop = True


# ============================================================================
# FILE ORGANIZER THREAD
# ============================================================================

class FileOrganizerThread(QThread):
    """Background thread for organizing files by extension"""

    progress = pyqtSignal(int, int)
    status_message = pyqtSignal(str)
    operation_complete = pyqtSignal(int, int)
    error_occurred = pyqtSignal(str)

    def __init__(self, source_path: str, dest_path: str, extensions: Set[str], 
                 scan_subfolders: bool = True, move_files: bool = True, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.dest_path = dest_path
        self.extensions = extensions
        self.scan_subfolders = scan_subfolders
        self.move_files = move_files
        self.should_stop = False

    def run(self):
        """Find and copy/move files"""
        processed_count = 0
        failed_count = 0
        files_to_process = []

        self.status_message.emit("Scanning for files...")

        try:
            if self.scan_subfolders:
                for root, dirs, files in os.walk(self.source_path):
                    if self.should_stop:
                        return

                    for filename in files:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in self.extensions:
                            files_to_process.append(os.path.join(root, filename))
            else:
                for item in os.listdir(self.source_path):
                    if self.should_stop:
                        return

                    filepath = os.path.join(self.source_path, item)
                    if os.path.isfile(filepath):
                        ext = os.path.splitext(item)[1].lower()
                        if ext in self.extensions:
                            files_to_process.append(filepath)

        except Exception as e:
            self.error_occurred.emit(f"Error scanning: {str(e)}")
            return

        total_files = len(files_to_process)
        operation_verb = "Moving" if self.move_files else "Copying"

        for i, source_file in enumerate(files_to_process):
            if self.should_stop:
                break

            try:
                filename = os.path.basename(source_file)
                dest_file = os.path.join(self.dest_path, filename)

                counter = 1
                base_name, ext = os.path.splitext(filename)
                while os.path.exists(dest_file):
                    new_name = f"{base_name}_{counter}{ext}"
                    dest_file = os.path.join(self.dest_path, new_name)
                    counter += 1

                if self.move_files:
                    shutil.move(source_file, dest_file)
                else:
                    shutil.copy2(source_file, dest_file)
                
                processed_count += 1
                self.progress.emit(i + 1, total_files)

            except Exception:
                failed_count += 1

        self.operation_complete.emit(processed_count, failed_count)

    def stop(self):
        """Stop operation"""
        self.should_stop = True

# ============================================================================
# CUSTOM TREE ITEM
# ============================================================================

class CheckableTreeItem(QTreeWidgetItem):
    """Tree item with checkbox"""

    def __init__(self, parent, file_data: Optional[DuplicateFile] = None):
        super().__init__(parent)
        self.file_data = file_data

        if file_data:
            self.setCheckState(0, Qt.CheckState.Unchecked)


# ============================================================================
# TAB 1: FILE EXPLORER WITH AUTO-INDEXING
# ============================================================================

class FileExplorerTab(QWidget):
    """File explorer with cached indexing and advanced filtering"""

    def __init__(self, db_manager: DatabaseManager, settings_tab=None, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.settings_tab = settings_tab
        self.file_index: List[FileEntry] = []
        self.displayed_files: List[FileEntry] = []
        self.indexer_thread: Optional[SystemIndexerThread] = None
        self.search_thread: Optional[FileSearchThread] = None
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.perform_search)

        self.init_ui()
        
        QTimer.singleShot(1000, self.start_auto_indexing_if_enabled)

    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()

        search_group = QGroupBox("Search & Filter")
        search_layout = QVBoxLayout()

        search_bar_layout = QHBoxLayout()
        search_label = QLabel("Search:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter filename to search...")
        self.search_input.textChanged.connect(self.on_search_text_changed)

        search_bar_layout.addWidget(search_label)
        search_bar_layout.addWidget(self.search_input)
        search_layout.addLayout(search_bar_layout)

        filter_layout = QHBoxLayout()
        
        filter_label = QLabel("Filter by Type:")
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("All Files")
        self.filter_combo.addItem("Images")
        self.filter_combo.addItem("Videos")
        self.filter_combo.addItem("Audio")
        self.filter_combo.addItem("Documents")
        self.filter_combo.addItem("Archives")
        self.filter_combo.addItem("Executables")
        self.filter_combo.addItem("Code")
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)
        
        self.show_all_btn = QPushButton("Show All Files")
        self.show_all_btn.clicked.connect(self.show_all_files)
        self.show_all_btn.setEnabled(False)
        
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(self.filter_combo, 1)
        filter_layout.addWidget(self.show_all_btn)

        search_layout.addLayout(filter_layout)

        status_layout = QHBoxLayout()
        self.index_status = QLabel("Initializing system index...")
        self.result_count_label = QLabel("Results: 0")
        
        self.force_reindex_btn = QPushButton("Force Full Re-index")
        self.force_reindex_btn.clicked.connect(self.force_full_reindex)
        self.force_reindex_btn.setEnabled(False)
        
        status_layout.addWidget(self.index_status)
        status_layout.addStretch()
        status_layout.addWidget(self.result_count_label)
        status_layout.addWidget(self.force_reindex_btn)

        search_layout.addLayout(status_layout)
        search_group.setLayout(search_layout)
        layout.addWidget(search_group)

        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout()

        self.file_table = QTableView()
        self.file_model = FileTableModel(self)
        self.file_table.setModel(self.file_model)
        self.file_table.setSortingEnabled(True)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setAlternatingRowColors(True)
        self.file_table.doubleClicked.connect(self.on_file_double_clicked)

        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        results_layout.addWidget(self.file_table)
        results_group.setLayout(results_layout)
        layout.addWidget(results_group)

        self.setLayout(layout)

    def start_auto_indexing_if_enabled(self):
        """Start automatic system-wide indexing if enabled in settings"""
        if self.settings_tab and not self.settings_tab.get_background_processing_enabled():
            self.index_status.setText("Background processing disabled in settings")
            cached_files = self.db_manager.load_file_index()
            if cached_files:
                self.file_index = cached_files
                self.index_status.setText(f"✓ Loaded {len(cached_files):,} files from cache")
                self.show_all_btn.setEnabled(True)
                self.force_reindex_btn.setEnabled(True)
            return
        
        self.start_auto_indexing()

    def start_auto_indexing(self):
        """Start automatic system-wide indexing"""
        self.index_status.setText("Loading index from cache...")
        
        # Pass db_path instead of db_manager
        self.indexer_thread = SystemIndexerThread(self.db_manager.db_path, force_full_index=False)
        self.indexer_thread.progress.connect(self.on_indexing_progress)
        self.indexer_thread.files_indexed.connect(self.on_files_indexed)
        self.indexer_thread.indexing_complete.connect(self.on_indexing_complete)
        self.indexer_thread.error_occurred.connect(self.on_indexing_error)
        self.indexer_thread.start()

    
    def force_full_reindex(self):
        """Force full system re-indexing"""
        reply = QMessageBox.question(
            self,
            "Force Full Re-index",
            "This will clear the cache and re-index all files.\n"
            "This may take several minutes.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.force_reindex_btn.setEnabled(False)
            self.index_status.setText("Starting full re-index...")
            
            # Pass db_path instead of db_manager
            self.indexer_thread = SystemIndexerThread(self.db_manager.db_path, force_full_index=True)
            self.indexer_thread.progress.connect(self.on_indexing_progress)
            self.indexer_thread.files_indexed.connect(self.on_files_indexed)
            self.indexer_thread.indexing_complete.connect(self.on_indexing_complete)
            self.indexer_thread.error_occurred.connect(self.on_indexing_error)
            self.indexer_thread.start()
    
    def on_indexing_progress(self, message: str):
        """Update indexing progress"""
        self.index_status.setText(message)

    def on_files_indexed(self, count: int):
        """Update file count"""
        self.index_status.setText(f"Indexing... {count:,} files found")
        if self.indexer_thread:
            self.file_index = self.indexer_thread.index.copy()

    def on_indexing_complete(self, total: int):
        """Handle indexing completion"""
        if self.indexer_thread:
            self.file_index = self.indexer_thread.index
        
        stats = self.db_manager.get_index_stats()
        
        self.index_status.setText(
            f"✓ Index ready: {stats['total_files']:,} files | "
            f"{stats['total_extensions']} types | "
            f"{self.format_size(stats['total_size'])}"
        )
        
        self.show_all_btn.setEnabled(True)
        self.force_reindex_btn.setEnabled(True)

    def on_indexing_error(self, error: str):
        """Handle indexing errors"""
        self.index_status.setText(f"Indexing error: {error}")

    def on_search_text_changed(self):
        """Handle search text changes"""
        self.search_timer.stop()
        self.search_timer.start(300)

    def perform_search(self):
        """Perform search"""
        query = self.search_input.text().strip()

        if not query:
            self.file_model.set_files([])
            self.result_count_label.setText("Results: 0")
            return

        if not self.file_index:
            self.index_status.setText("Please wait for indexing to complete...")
            return

        if self.search_thread and self.search_thread.isRunning():
            self.search_thread.stop()
            self.search_thread.wait()

        self.search_thread = FileSearchThread(self.file_index, query, "")
        self.search_thread.results_ready.connect(self.on_search_results)
        self.search_thread.search_complete.connect(self.on_search_complete)
        self.search_thread.start()

    def on_search_results(self, results: List[FileEntry]):
        """Handle search results"""
        current_files = self.file_model.files.copy()
        current_files.extend(results)
        self.file_model.set_files(current_files)
        self.result_count_label.setText(f"Results: {len(current_files)}")

    def on_search_complete(self, total: int):
        """Handle search completion"""
        pass
    
    def show_all_files(self):
        """Display all indexed files"""
        if not self.file_index:
            return
        
        self.file_model.set_files(self.file_index[:10000])
        self.result_count_label.setText(f"Showing: {min(len(self.file_index), 10000):,} of {len(self.file_index):,}")
    
    def apply_filter(self):
        """Apply file type filter"""
        filter_type = self.filter_combo.currentText()
        
        if filter_type == "All Files":
            if self.search_input.text().strip():
                self.perform_search()
            else:
                self.file_model.set_files([])
                self.result_count_label.setText("Results: 0")
            return
        
        extension_map = {
            "Images": ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff'],
            "Videos": ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'],
            "Audio": ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a'],
            "Documents": ['.pdf', '.doc', '.docx', '.txt', '.rtf', '.odt'],
            "Archives": ['.zip', '.rar', '.7z', '.tar', '.gz'],
            "Executables": ['.exe', '.msi', '.app', '.deb'],
            "Code": ['.py', '.js', '.html', '.css', '.java', '.cpp', '.c', '.h']
        }
        
        extensions = extension_map.get(filter_type, [])
        filtered = [f for f in self.file_index if f.extension in extensions]
        
        self.file_model.set_files(filtered[:5000])
        self.result_count_label.setText(f"Showing: {min(len(filtered), 5000):,} of {len(filtered):,} {filter_type}")

    def on_file_double_clicked(self, index: QModelIndex):
        """Open file on double-click"""
        file_entry = self.file_model.get_file(index.row())
        if not file_entry:
            return

        try:
            if sys.platform == 'win32':
                os.startfile(file_entry.path)
            elif sys.platform == 'darwin':
                os.system(f'open "{file_entry.path}"')
            else:
                os.system(f'xdg-open "{file_entry.path}"')
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Cannot open file:\n{str(e)}")
    
    @staticmethod
    def format_size(size: int) -> str:
        """Format size"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"


# ============================================================================
# TAB 2: FILE ORGANIZER
# ============================================================================

class FileOrganizerTab(QWidget):
    """File organizer by extension with Copy and Move options"""

    EXTENSION_CATEGORIES = {
        "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff"],
        "Videos": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"],
        "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"],
        "Documents": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"],
        "Spreadsheets": [".xls", ".xlsx", ".csv", ".ods"],
        "Presentations": [".ppt", ".pptx", ".odp"],
        "Archives": [".zip", ".rar", ".7z", ".tar", ".gz"],
        "Executables": [".exe", ".msi", ".app", ".deb"],
        "Scripts": [".py", ".js", ".sh", ".bat", ".ps1"],
        "Web": [".html", ".htm", ".css", ".xml", ".json"],
    }

    def __init__(self, db_manager: DatabaseManager, parent=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.organizer_thread: Optional[FileOrganizerThread] = None
        self.selected_extensions: Set[str] = set()
        self.init_ui()
        self.load_saved_extensions()

    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()

        ext_group = QGroupBox("Select File Extensions")
        ext_layout = QVBoxLayout()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QGridLayout()

        self.extension_checkboxes: Dict[str, QCheckBox] = {}
        row = 0
        col = 0

        for category, extensions in self.EXTENSION_CATEGORIES.items():
            category_label = QLabel(f"<b>{category}</b>")
            scroll_layout.addWidget(category_label, row, col, 1, 2)
            row += 1

            for ext in extensions:
                checkbox = QCheckBox(ext)
                checkbox.stateChanged.connect(self.update_selected_extensions)
                self.extension_checkboxes[ext] = checkbox
                scroll_layout.addWidget(checkbox, row, col)

                col += 1
                if col >= 4:
                    col = 0
                    row += 1

            if col != 0:
                col = 0
                row += 1

        scroll_content.setLayout(scroll_layout)
        scroll.setWidget(scroll_content)
        ext_layout.addWidget(scroll)

        custom_layout = QHBoxLayout()
        self.custom_ext_input = QLineEdit()
        self.custom_ext_input.setPlaceholderText("Custom extension (e.g., .xyz)")
        add_custom_btn = QPushButton("Add")
        add_custom_btn.clicked.connect(self.add_custom_extension)

        custom_layout.addWidget(self.custom_ext_input)
        custom_layout.addWidget(add_custom_btn)
        ext_layout.addLayout(custom_layout)

        self.selection_label = QLabel("Selected: 0 extensions")
        ext_layout.addWidget(self.selection_label)

        ext_group.setLayout(ext_layout)
        layout.addWidget(ext_group)

        paths_group = QGroupBox("Source and Destination")
        paths_layout = QGridLayout()

        self.source_input = QLineEdit()
        browse_source_btn = QPushButton("Browse")
        browse_source_btn.clicked.connect(self.browse_source)

        self.subfolder_check = QCheckBox("Include subfolders")
        self.subfolder_check.setChecked(True)

        self.dest_input = QLineEdit()
        browse_dest_btn = QPushButton("Browse")
        browse_dest_btn.clicked.connect(self.browse_destination)

        paths_layout.addWidget(QLabel("Source:"), 0, 0)
        paths_layout.addWidget(self.source_input, 0, 1)
        paths_layout.addWidget(browse_source_btn, 0, 2)
        paths_layout.addWidget(self.subfolder_check, 1, 1)
        paths_layout.addWidget(QLabel("Destination:"), 2, 0)
        paths_layout.addWidget(self.dest_input, 2, 1)
        paths_layout.addWidget(browse_dest_btn, 2, 2)

        paths_group.setLayout(paths_layout)
        layout.addWidget(paths_group)

        operation_group = QGroupBox("Operation")
        operation_layout = QHBoxLayout()
        
        self.operation_button_group = QButtonGroup()
        
        self.move_radio = QRadioButton("Move Files")
        self.move_radio.setChecked(True)
        self.move_radio.setToolTip("Move files to destination (removes from source)")
        
        self.copy_radio = QRadioButton("Copy Files")
        self.copy_radio.setToolTip("Copy files to destination (keeps original)")
        
        self.operation_button_group.addButton(self.move_radio)
        self.operation_button_group.addButton(self.copy_radio)
        
        operation_layout.addWidget(self.move_radio)
        operation_layout.addWidget(self.copy_radio)
        operation_layout.addStretch()
        
        operation_group.setLayout(operation_layout)
        layout.addWidget(operation_group)

        action_layout = QHBoxLayout()
        self.execute_button = QPushButton("Execute Operation")
        self.execute_button.clicked.connect(self.execute_operation)
        self.execute_button.setStyleSheet("background-color: #27ae60; font-weight: bold;")

        action_layout.addWidget(self.execute_button)
        action_layout.addStretch()
        layout.addLayout(action_layout)

        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout()

        self.progress_bar = QProgressBar()
        self.status_text = QLabel("Ready")

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_text)

        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)

        self.setLayout(layout)

    def update_selected_extensions(self):
        """Update selected extensions"""
        self.selected_extensions.clear()
        for ext, checkbox in self.extension_checkboxes.items():
            if checkbox.isChecked():
                self.selected_extensions.add(ext)

        self.selection_label.setText(f"Selected: {len(self.selected_extensions)} extensions")
        self.db_manager.save_selected_extensions(self.selected_extensions)

    def add_custom_extension(self):
        """Add custom extension"""
        ext = self.custom_ext_input.text().strip()
        if ext and ext.startswith('.'):
            self.selected_extensions.add(ext.lower())
            self.selection_label.setText(f"Selected: {len(self.selected_extensions)} extensions")
            self.custom_ext_input.clear()
            self.db_manager.save_selected_extensions(self.selected_extensions)

    def browse_source(self):
        """Browse source"""
        folder = QFileDialog.getExistingDirectory(self, "Select Source")
        if folder:
            self.source_input.setText(folder)

    def browse_destination(self):
        """Browse destination"""
        folder = QFileDialog.getExistingDirectory(self, "Select Destination")
        if folder:
            self.dest_input.setText(folder)

    def execute_operation(self):
        """Execute copy or move operation"""
        if not self.selected_extensions:
            QMessageBox.warning(self, "Error", "Select at least one extension")
            return

        source = self.source_input.text()
        dest = self.dest_input.text()

        if not source or not dest:
            QMessageBox.warning(self, "Error", "Select source and destination")
            return
        
        is_move = self.move_radio.isChecked()
        operation_name = "Move" if is_move else "Copy"

        reply = QMessageBox.question(
            self, "Confirm",
            f"{operation_name} files from:\n{source}\n\nTo:\n{dest}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.execute_button.setEnabled(False)

        self.organizer_thread = FileOrganizerThread(
            source, dest, self.selected_extensions, 
            self.subfolder_check.isChecked(), is_move
        )
        self.organizer_thread.progress.connect(self.on_progress)
        self.organizer_thread.status_message.connect(self.on_status)
        self.organizer_thread.operation_complete.connect(self.on_complete)
        self.organizer_thread.start()

    def on_progress(self, current: int, total: int):
        """Update progress"""
        if total > 0:
            self.progress_bar.setValue(int((current / total) * 100))

    def on_status(self, message: str):
        """Update status"""
        self.status_text.setText(message)

    def on_complete(self, processed: int, failed: int):
        """Handle completion"""
        self.progress_bar.setValue(100)
        self.execute_button.setEnabled(True)
        operation_name = "Moved" if self.move_radio.isChecked() else "Copied"
        QMessageBox.information(self, "Complete", f"{operation_name}: {processed}\nFailed: {failed}")
    
    def load_saved_extensions(self):
        """Load saved extension selections"""
        saved_extensions = self.db_manager.load_selected_extensions()
        
        for ext in saved_extensions:
            if ext in self.extension_checkboxes:
                self.extension_checkboxes[ext].setChecked(True)
        
        self.selected_extensions = saved_extensions
        self.selection_label.setText(f"Selected: {len(self.selected_extensions)} extensions")


# ============================================================================
# TAB 3: INTELLIGENT DUPLICATE FINDER
# ============================================================================

class DuplicateFinderTab(QWidget):
    """Advanced duplicate finder with Fast and Deep scan modes"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_folder: Optional[str] = None
        self.duplicate_groups: Dict[str, DuplicateGroup] = {}
        self.scanner_thread = None
        self.deletion_thread: Optional[FileDeletionThread] = None

        self.init_ui()

    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()

        # Folder Selection
        folder_group = QGroupBox("1. Select Folder to Scan")
        folder_layout = QHBoxLayout()

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setStyleSheet("padding: 5px; background-color: #858585; color:#000000;font-weight:bold; border-radius: 3px;")

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_folder)

        folder_layout.addWidget(self.folder_label, 1)
        folder_layout.addWidget(browse_btn)

        folder_group.setLayout(folder_layout)
        layout.addWidget(folder_group)

        # Scan Mode Selection
        mode_group = QGroupBox("2. Choose Scan Mode")
        mode_layout = QVBoxLayout()

        # Fast Scan
        self.fast_radio = QRadioButton("⚡ Fast Scan (Recommended)")
        self.fast_radio.setChecked(True)
        self.fast_radio.setToolTip(
            "Fast Scan Mode:\n"
            "• Uses intelligent filename normalization\n"
            "• Detects common copy patterns like (1), (2), - Copy, etc.\n"
            "• Optional size matching for better accuracy\n"
            "• Speed: Very Fast (100-1000 files/sec)\n"
            "• Accuracy: Good for typical duplicate scenarios\n"
            "• Best for: Quick cleanup of copied/downloaded files"
        )
        mode_layout.addWidget(self.fast_radio)

        # Fast scan options
        fast_options = QHBoxLayout()
        fast_options.addSpacing(30)
        self.size_filter_check = QCheckBox("Match file size")
        self.size_filter_check.setToolTip(
            "When enabled:\n"
            "• Only files with identical names AND sizes are considered duplicates\n"
            "• Significantly reduces false positives\n"
            "• Slightly slower but more accurate\n"
            "• Recommended for mixed file collections"
        )
        fast_options.addWidget(self.size_filter_check)
        fast_options.addStretch()
        mode_layout.addLayout(fast_options)

        mode_layout.addSpacing(10)

        # Deep Scan
        self.deep_radio = QRadioButton("🔬 Deep Scan (Content-Based)")
        self.deep_radio.setToolTip(
            "Deep Scan Mode:\n"
            "• Analyzes actual file content using cryptographic hashing\n"
            "• Finds files with identical content even if names differ\n"
            "• 100% accurate for binary duplicates\n"
            "• Speed: Slow (depends on file sizes)\n"
            "• Best for: Finding hidden duplicates, verifying backups"
        )
        mode_layout.addWidget(self.deep_radio)

        # Deep scan options
        deep_options = QVBoxLayout()
        deep_options.addSpacing(5)
        
        hash_layout = QHBoxLayout()
        hash_layout.addSpacing(30)
        hash_layout.addWidget(QLabel("Hash Algorithm:"))
        
        self.hash_combo = QComboBox()
        self.hash_combo.addItems(["MD5", "SHA-1", "SHA-256"])
        self.hash_combo.setCurrentText("MD5")
        self.hash_combo.setToolTip(
            "MD5: Fastest, good for most uses\n"
            "SHA-1: Balanced speed and security\n"
            "SHA-256: Slowest but most secure\n\n"
            "All algorithms provide 100% accuracy for duplicate detection"
        )
        hash_layout.addWidget(self.hash_combo)
        hash_layout.addStretch()
        deep_options.addLayout(hash_layout)

        mode_layout.addLayout(deep_options)

        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)

        # Action Buttons
        action_layout = QHBoxLayout()
        
        self.scan_btn = QPushButton("▶ Start Scan")
        self.scan_btn.clicked.connect(self.start_scan)
        self.scan_btn.setEnabled(False)
        self.scan_btn.setStyleSheet("background-color: #27ae60; font-weight: bold; font-size: 12pt;")
        
        self.cancel_btn = QPushButton("■ Cancel")
        self.cancel_btn.clicked.connect(self.cancel_scan)
        self.cancel_btn.setEnabled(False)

        action_layout.addWidget(self.scan_btn)
        action_layout.addWidget(self.cancel_btn)
        action_layout.addStretch()

        layout.addLayout(action_layout)

        # Statistics
        stats_group = QGroupBox("Scan Statistics")
        stats_layout = QHBoxLayout()
        
        self.stats_label = QLabel("Ready to scan")
        self.stats_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        
        stats_layout.addWidget(self.stats_label)
        
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # Results
        results_group = QGroupBox("Duplicate Files Found")
        results_layout = QVBoxLayout()

        # Controls
        controls = QHBoxLayout()
        
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all)
        self.select_all_btn.setEnabled(False)
        
        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        self.deselect_all_btn.setEnabled(False)
        
        self.expand_all_btn = QPushButton("Expand All")
        self.expand_all_btn.clicked.connect(lambda: self.results_tree.expandAll())
        self.expand_all_btn.setEnabled(False)
        
        self.collapse_all_btn = QPushButton("Collapse All")
        self.collapse_all_btn.clicked.connect(lambda: self.results_tree.collapseAll())
        self.collapse_all_btn.setEnabled(False)

        controls.addWidget(self.select_all_btn)
        controls.addWidget(self.deselect_all_btn)
        controls.addWidget(self.expand_all_btn)
        controls.addWidget(self.collapse_all_btn)
        controls.addStretch()

        results_layout.addLayout(controls)

        # Tree
        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabels(['File Name', 'Size', 'Path', 'Modified'])
        self.results_tree.setAlternatingRowColors(True)
        self.results_tree.itemChanged.connect(self.update_selection_count)

        header = self.results_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        results_layout.addWidget(self.results_tree)

        results_group.setLayout(results_layout)
        layout.addWidget(results_group, 1)

        # Actions
        actions_group = QGroupBox("Actions")
        actions_layout = QHBoxLayout()

        self.delete_btn = QPushButton("🗑️ Delete Selected Files")
        self.delete_btn.clicked.connect(self.delete_selected)
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet("background-color: #e74c3c; font-weight: bold;")

        self.clear_btn = QPushButton("Clear Results")
        self.clear_btn.clicked.connect(self.clear_results)
        self.clear_btn.setEnabled(False)

        self.selection_label = QLabel("Selected: 0 files (0 B)")
        self.selection_label.setStyleSheet("font-weight: bold;")

        actions_layout.addWidget(self.delete_btn)
        actions_layout.addWidget(self.clear_btn)
        actions_layout.addStretch()
        actions_layout.addWidget(self.selection_label)

        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)

        # Progress
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout()

        self.progress_bar = QProgressBar()
        self.status_label = QLabel("Ready")

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_label)

        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)

        # ===== WRAP EVERYTHING IN A SCROLLABLE CONTAINER =====
        # Create container widget for all content
        content_widget = QWidget()
        content_widget.setLayout(layout)
        
        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setWidget(content_widget)
        
        # Set scroll area as main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        
        self.setLayout(main_layout)

    def browse_folder(self):
        """Browse for folder"""
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.current_folder = folder
            self.folder_label.setText(folder)
            self.scan_btn.setEnabled(True)

    def start_scan(self):
        """Start scanning"""
        if not self.current_folder:
            return

        self.clear_results()
        self.scan_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)

        if self.fast_radio.isChecked():
            # Fast scan
            use_size = self.size_filter_check.isChecked()
            self.status_label.setText("Starting fast scan...")
            
            self.scanner_thread = FastDuplicateScannerThread(
                self.current_folder, use_size
            )
        else:
            # Deep scan
            hash_alg = self.hash_combo.currentText().lower().replace('-', '')
            self.status_label.setText("Starting deep scan (this may take a while)...")
            
            self.scanner_thread = DeepDuplicateScannerThread(
                self.current_folder, hash_alg
            )

        self.scanner_thread.progress.connect(self.on_progress)
        self.scanner_thread.files_scanned.connect(self.on_files_scanned)
        self.scanner_thread.scan_complete.connect(self.on_scan_complete)
        self.scanner_thread.error_occurred.connect(self.on_error)
        self.scanner_thread.start()

    def cancel_scan(self):
        """Cancel scan"""
        if self.scanner_thread:
            self.scanner_thread.stop()
            self.scanner_thread.wait()
        
        self.reset_ui()
        self.status_label.setText("Scan cancelled")

    def on_progress(self, message: str):
        """Update progress"""
        self.status_label.setText(message)

    def on_files_scanned(self, count: int):
        """Update file count"""
        self.stats_label.setText(f"Scanned: {count:,} files")

    def on_scan_complete(self, duplicates: Dict):
        """Handle scan completion"""
        self.reset_ui()

        if not duplicates:
            self.stats_label.setText("No duplicates found")
            self.status_label.setText("Scan complete - no duplicates")
            QMessageBox.information(self, "Complete", "No duplicate files found.")
            return

        # Convert to groups
        for key, files in duplicates.items():
            group = DuplicateGroup(key=key, files=files)
            self.duplicate_groups[key] = group

        # Update UI
        self.populate_results()

        # Statistics
        total_groups = len(duplicates)
        total_files = sum(len(files) for files in duplicates.values())
        total_size = sum(sum(f.size for f in files) for files in duplicates.values())
        wasted = sum(g.wasted_space() for g in self.duplicate_groups.values())

        self.stats_label.setText(
            f"Found {total_groups:,} duplicate groups • "
            f"{total_files:,} files • "
            f"Wasted space: {self.format_size(wasted)}"
        )
        self.status_label.setText("Scan complete")
        self.progress_bar.setValue(100)

        # Enable controls
        self.select_all_btn.setEnabled(True)
        self.deselect_all_btn.setEnabled(True)
        self.expand_all_btn.setEnabled(True)
        self.collapse_all_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)

        QMessageBox.information(
            self,
            "Scan Complete",
            f"Found {total_groups:,} duplicate groups.\n"
            f"Total: {total_files:,} files\n"
            f"Wasted space: {self.format_size(wasted)}"
        )

    def on_error(self, error: str):
        """Handle error"""
        self.reset_ui()
        self.status_label.setText(f"Error: {error}")
        QMessageBox.critical(self, "Error", error)

    def populate_results(self):
        """Populate results tree"""
        self.results_tree.clear()
        self.results_tree.blockSignals(True)

        sorted_groups = sorted(
            self.duplicate_groups.values(),
            key=lambda g: g.wasted_space(),
            reverse=True
        )

        for group in sorted_groups:
            # Group header
            group_item = QTreeWidgetItem(self.results_tree)
            
            if self.deep_radio.isChecked():
                display_name = f"Content Hash: {group.key[:16]}..."
            else:
                display_name = group.key
            
            group_item.setText(0, f"📁 {display_name} ({group.count()} files)")
            group_item.setText(1, self.format_size(group.total_size()))
            group_item.setText(2, f"Wasted: {self.format_size(group.wasted_space())}")

            font = QFont()
            font.setBold(True)
            for col in range(4):
                group_item.setFont(col, font)
                group_item.setBackground(col, QColor(135, 135, 135))
                group_item.setForeground(col,QColor(0, 0, 0))

            # Files
            for file in sorted(group.files, key=lambda f: f.size, reverse=True):
                file_item = CheckableTreeItem(group_item, file)
                file_item.setText(0, file.name)
                file_item.setText(1, file.size_formatted())
                file_item.setText(2, file.path)
                file_item.setText(3, file.modified_formatted())
                file_item.setToolTip(2, file.path)

        self.results_tree.blockSignals(False)

        # Expand first few
        root = self.results_tree.invisibleRootItem()
        for i in range(min(3, root.childCount())):
            root.child(i).setExpanded(True)

    def select_all(self):
        """Select all files"""
        self.results_tree.blockSignals(True)
        
        root = self.results_tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if isinstance(item, CheckableTreeItem):
                    item.setCheckState(0, Qt.CheckState.Checked)
        
        self.results_tree.blockSignals(False)
        self.update_selection_count()

    def deselect_all(self):
        """Deselect all"""
        self.results_tree.blockSignals(True)
        
        root = self.results_tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if isinstance(item, CheckableTreeItem):
                    item.setCheckState(0, Qt.CheckState.Unchecked)
        
        self.results_tree.blockSignals(False)
        self.update_selection_count()

    def update_selection_count(self):
        """Update selection label"""
        selected = self.get_selected_files()
        count = len(selected)
        size = sum(f.size for f in selected)

        self.selection_label.setText(f"Selected: {count} files ({self.format_size(size)})")
        self.delete_btn.setEnabled(count > 0)

    def get_selected_files(self) -> List[DuplicateFile]:
        """Get selected files"""
        selected = []
        
        root = self.results_tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                item = group.child(j)
                if isinstance(item, CheckableTreeItem):
                    if item.checkState(0) == Qt.CheckState.Checked and item.file_data:
                        selected.append(item.file_data)
        
        return selected

    def delete_selected(self):
        """Delete selected files"""
        selected = self.get_selected_files()
        
        if not selected:
            return

        size = sum(f.size for f in selected)
        
        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Delete {len(selected)} files ({self.format_size(size)})?\n\n"
            f"This action CANNOT be undone!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.delete_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)

        self.deletion_thread = FileDeletionThread(selected)
        self.deletion_thread.progress.connect(self.on_delete_progress)
        self.deletion_thread.deletion_complete.connect(self.on_delete_complete)
        self.deletion_thread.start()

    def on_delete_progress(self, current: int, total: int):
        """Update deletion progress"""
        if total > 0:
            self.progress_bar.setValue(int((current / total) * 100))

    def on_delete_complete(self, deleted: int, failed: int):
        """Handle deletion completion"""
        self.progress_bar.setValue(100)
        self.scan_btn.setEnabled(True)

        QMessageBox.information(
            self,
            "Complete",
            f"Deleted: {deleted} files\nFailed: {failed} files"
        )

        # Rescan
        if self.current_folder:
            QTimer.singleShot(500, self.start_scan)

    def clear_results(self):
        """Clear results"""
        self.results_tree.clear()
        self.duplicate_groups.clear()
        self.stats_label.setText("Ready to scan")
        self.selection_label.setText("Selected: 0 files (0 B)")
        self.progress_bar.setValue(0)
        self.status_label.setText("Ready")

        self.select_all_btn.setEnabled(False)
        self.deselect_all_btn.setEnabled(False)
        self.expand_all_btn.setEnabled(False)
        self.collapse_all_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)

    def reset_ui(self):
        """Reset UI after scan"""
        self.scan_btn.setEnabled(bool(self.current_folder))
        self.cancel_btn.setEnabled(False)

    @staticmethod
    def format_size(size: int) -> str:
        """Format size"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"


# ============================================================================
# TAB 4: MEDIA FORMAT CONVERTER
# ============================================================================

class MediaConverterTab(QWidget):
    """Real media format converter for images, videos, and audio"""
    
    # Supported formats
    IMAGE_INPUT_FORMATS = ['.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.tif']
    IMAGE_OUTPUT_FORMATS = ['jpg', 'png', 'webp']
    
    VIDEO_INPUT_FORMATS = ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv']
    VIDEO_OUTPUT_FORMATS = ['mp4', 'mkv', 'webm']
    
    AUDIO_INPUT_FORMATS = ['.mp3', '.wav', '.aac', '.flac', '.ogg', '.m4a', '.wma']
    AUDIO_OUTPUT_FORMATS = ['mp3', 'wav', 'aac', 'flac']
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.media_files: List[MediaFile] = []
        self.conversion_thread: Optional[MediaConversionThread] = None
        
        self.init_ui()
        self.check_dependencies()
    
    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()
        
        # Warning banner
        warning = QLabel(
            "⚠️ <b>Real Media Conversion</b><br>"
            "This tool performs actual format conversion by decoding and re-encoding files. "
            "Conversion may take time and consume CPU resources. "
            "Always test with a few files first!"
        )
        warning.setStyleSheet(
            "padding: 10px; background-color: #fff3cd; border: 1px solid #ffc107; "
            "border-radius: 5px; color: #856404;"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        
        # File Selection
        selection_group = QGroupBox("1. Select Files to Convert")
        selection_layout = QVBoxLayout()
        
        buttons_layout = QHBoxLayout()
        
        self.add_files_btn = QPushButton("Add Files")
        self.add_files_btn.clicked.connect(self.add_files)
        self.add_files_btn.setToolTip("Select individual media files to convert")
        
        self.add_folder_btn = QPushButton("Add Folder")
        self.add_folder_btn.clicked.connect(self.add_folder)
        self.add_folder_btn.setToolTip("Scan a folder recursively for media files")
        
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self.clear_files)
        self.clear_btn.setToolTip("Remove all files from the list")
        
        buttons_layout.addWidget(self.add_files_btn)
        buttons_layout.addWidget(self.add_folder_btn)
        buttons_layout.addWidget(self.clear_btn)
        buttons_layout.addStretch()
        
        selection_layout.addLayout(buttons_layout)
        
        # Files table
        self.files_table = QTableWidget()
        self.files_table.setColumnCount(4)
        self.files_table.setHorizontalHeaderLabels(['Filename', 'Source Format', 'Target Format', 'Status'])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        
        selection_layout.addWidget(self.files_table)
        
        self.file_count_label = QLabel("Files: 0")
        self.file_count_label.setStyleSheet("font-weight: bold;")
        selection_layout.addWidget(self.file_count_label)
        
        selection_group.setLayout(selection_layout)
        layout.addWidget(selection_group)
        
        # Conversion Settings
        settings_group = QGroupBox("2. Conversion Settings")
        settings_layout = QVBoxLayout()
        
        # Media type tabs
        media_tabs = QTabWidget()
        
        # === IMAGE SETTINGS ===
        image_widget = QWidget()
        image_layout = QGridLayout()
        
        image_layout.addWidget(QLabel("Output Format:"), 0, 0)
        self.image_format_combo = QComboBox()
        self.image_format_combo.addItems(self.IMAGE_OUTPUT_FORMATS)
        self.image_format_combo.setToolTip(
            "JPG: Best for photos, smallest size, lossy\n"
            "PNG: Best for graphics, lossless, larger size\n"
            "WebP: Modern format, good compression, not universally supported"
        )
        image_layout.addWidget(self.image_format_combo, 0, 1)
        
        image_layout.addWidget(QLabel("Quality:"), 1, 0)
        self.image_quality_spin = QSpinBox()
        self.image_quality_spin.setRange(1, 100)
        self.image_quality_spin.setValue(85)
        self.image_quality_spin.setSuffix(" %")
        self.image_quality_spin.setToolTip(
            "Quality setting for JPG and WebP formats\n"
            "85-95: High quality (recommended)\n"
            "70-84: Medium quality\n"
            "Below 70: Low quality, smaller files"
        )
        image_layout.addWidget(self.image_quality_spin, 1, 1)
        
        image_layout.setRowStretch(2, 1)
        image_widget.setLayout(image_layout)
        media_tabs.addTab(image_widget, "🖼️ Images")
        
        # === VIDEO SETTINGS ===
        video_widget = QWidget()
        video_layout = QGridLayout()
        
        video_layout.addWidget(QLabel("Output Format:"), 0, 0)
        self.video_format_combo = QComboBox()
        self.video_format_combo.addItems(self.VIDEO_OUTPUT_FORMATS)
        self.video_format_combo.setToolTip(
            "MP4: Most compatible, widely supported\n"
            "MKV: Feature-rich container, less compatible\n"
            "WebM: Web-optimized, modern browsers only"
        )
        video_layout.addWidget(self.video_format_combo, 0, 1)
        
        video_layout.addWidget(QLabel("Quality Preset:"), 1, 0)
        self.video_quality_combo = QComboBox()
        self.video_quality_combo.addItems(['Fast (Lower Quality)', 'Medium (Balanced)', 'Slow (Best Quality)'])
        self.video_quality_combo.setCurrentIndex(1)
        self.video_quality_combo.setToolTip(
            "Fast: Quick conversion, larger files, lower quality\n"
            "Medium: Balanced speed and quality (recommended)\n"
            "Slow: Best quality, smaller files, takes longer"
        )
        video_layout.addWidget(self.video_quality_combo, 1, 1)
        
        video_layout.addWidget(QLabel("Resolution:"), 2, 0)
        self.video_resolution_combo = QComboBox()
        self.video_resolution_combo.addItems(['Keep Original', '1920x1080 (1080p)', '1280x720 (720p)', '854x480 (480p)'])
        self.video_resolution_combo.setToolTip(
            "Keep Original: Maintain source resolution\n"
            "Lower resolutions reduce file size and processing time"
        )
        video_layout.addWidget(self.video_resolution_combo, 2, 1)
        
        video_layout.setRowStretch(3, 1)
        video_widget.setLayout(video_layout)
        media_tabs.addTab(video_widget, "🎬 Videos")
        
        # === AUDIO SETTINGS ===
        audio_widget = QWidget()
        audio_layout = QGridLayout()
        
        audio_layout.addWidget(QLabel("Output Format:"), 0, 0)
        self.audio_format_combo = QComboBox()
        self.audio_format_combo.addItems(self.AUDIO_OUTPUT_FORMATS)
        self.audio_format_combo.setToolTip(
            "MP3: Most compatible, good compression, lossy\n"
            "AAC: Better quality than MP3 at same bitrate\n"
            "FLAC: Lossless, larger files, best quality\n"
            "WAV: Uncompressed, very large files"
        )
        audio_layout.addWidget(self.audio_format_combo, 0, 1)
        
        audio_layout.addWidget(QLabel("Bitrate:"), 1, 0)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(['128k', '192k', '256k', '320k'])
        self.audio_bitrate_combo.setCurrentIndex(1)
        self.audio_bitrate_combo.setToolTip(
            "128k: Acceptable quality, smaller files\n"
            "192k: Good quality (recommended)\n"
            "256k: Very good quality\n"
            "320k: Excellent quality, larger files"
        )
        audio_layout.addWidget(self.audio_bitrate_combo, 1, 1)
        
        audio_layout.addWidget(QLabel("Sample Rate:"), 2, 0)
        self.audio_sample_rate_combo = QComboBox()
        self.audio_sample_rate_combo.addItems(['Keep Original', '44100 Hz (CD Quality)', '48000 Hz (Studio)'])
        self.audio_sample_rate_combo.setToolTip(
            "Keep Original: Maintain source sample rate\n"
            "44100 Hz: CD quality, standard for music\n"
            "48000 Hz: Professional audio standard"
        )
        audio_layout.addWidget(self.audio_sample_rate_combo, 2, 1)
        
        audio_layout.setRowStretch(3, 1)
        audio_widget.setLayout(audio_layout)
        media_tabs.addTab(audio_widget, "🎵 Audio")
        
        settings_layout.addWidget(media_tabs)
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        # Output Settings
        output_group = QGroupBox("3. Output Settings")
        output_layout = QGridLayout()
        
        output_layout.addWidget(QLabel("Output Directory:"), 0, 0)
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setPlaceholderText("Select output directory...")
        output_layout.addWidget(self.output_dir_input, 0, 1)
        
        browse_output_btn = QPushButton("Browse")
        browse_output_btn.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(browse_output_btn, 0, 2)
        
        self.delete_originals_check = QCheckBox("Delete original files after successful conversion")
        self.delete_originals_check.setToolTip(
            "⚠️ WARNING: Original files will be permanently deleted!\n"
            "Only enable if you're sure you don't need the originals."
        )
        self.delete_originals_check.setStyleSheet("color: #d32f2f; font-weight: bold;")
        output_layout.addWidget(self.delete_originals_check, 1, 0, 1, 3)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
        
        # Action Buttons
        action_layout = QHBoxLayout()
        
        self.convert_btn = QPushButton("🔄 Start Conversion")
        self.convert_btn.clicked.connect(self.start_conversion)
        self.convert_btn.setEnabled(False)
        self.convert_btn.setStyleSheet("background-color: #4caf50; font-weight: bold; font-size: 12pt;")
        
        self.cancel_btn = QPushButton("■ Cancel")
        self.cancel_btn.clicked.connect(self.cancel_conversion)
        self.cancel_btn.setEnabled(False)
        
        action_layout.addWidget(self.convert_btn)
        action_layout.addWidget(self.cancel_btn)
        action_layout.addStretch()
        
        layout.addLayout(action_layout)
        
        # Progress
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_label)
        
        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)
        
        # ===== WRAP EVERYTHING IN A SCROLLABLE CONTAINER =====
        # Create container widget for all content
        content_widget = QWidget()
        content_widget.setLayout(layout)
        
        # Create scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setWidget(content_widget)
        
        # Set scroll area as main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        
        self.setLayout(main_layout)
    
    def check_dependencies(self):
        """Check for required dependencies"""
        issues = []
        
        if not PILLOW_AVAILABLE:
            issues.append("Pillow library not installed (required for image conversion)")
        
        if not MediaConverter.check_ffmpeg():
            issues.append("ffmpeg not found in system PATH (required for video/audio conversion)")
        
        if issues:
            QMessageBox.warning(
                self,
                "Missing Dependencies",
                "<b>Some features will be unavailable:</b><br><br>" +
                "<br>".join(f"• {issue}" for issue in issues) +
                "<br><br><b>Installation Instructions:</b><br>"
                "• Pillow: pip install Pillow<br>"
                "• ffmpeg: Download from ffmpeg.org and add to system PATH"
            )
    
    def add_files(self):
        """Add individual files"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Media Files",
            "",
            "All Media (*.jpg *.jpeg *.png *.bmp *.webp *.tiff *.mp4 *.mkv *.avi *.mov *.webm *.mp3 *.wav *.aac *.flac *.ogg *.m4a);;"
            "Images (*.jpg *.jpeg *.png *.bmp *.webp *.tiff);;"
            "Videos (*.mp4 *.mkv *.avi *.mov *.webm);;"
            "Audio (*.mp3 *.wav *.aac *.flac *.ogg *.m4a);;"
            "All Files (*.*)"
        )
        
        if files:
            for file_path in files:
                self.add_media_file(file_path)
            
            self.update_table()
    
    def add_folder(self):
        """Add all media files from folder"""
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        
        if folder:
            count = 0
            for root, dirs, files in os.walk(folder):
                for filename in files:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in (self.IMAGE_INPUT_FORMATS + self.VIDEO_INPUT_FORMATS + self.AUDIO_INPUT_FORMATS):
                        filepath = os.path.join(root, filename)
                        self.add_media_file(filepath)
                        count += 1
            
            self.update_table()
            
            if count > 0:
                QMessageBox.information(self, "Files Added", f"Added {count} media files from folder.")
            else:
                QMessageBox.information(self, "No Files Found", "No supported media files found in selected folder.")
    
    def add_media_file(self, file_path: str):
        """Add a media file to the list"""
        # Check if already added
        if any(mf.source_path == file_path for mf in self.media_files):
            return
        
        ext = os.path.splitext(file_path)[1].lower().lstrip('.')
        
        # Determine target format
        if ext in [fmt.lstrip('.') for fmt in self.IMAGE_INPUT_FORMATS]:
            target_format = self.image_format_combo.currentText()
        elif ext in [fmt.lstrip('.') for fmt in self.VIDEO_INPUT_FORMATS]:
            target_format = self.video_format_combo.currentText()
        elif ext in [fmt.lstrip('.') for fmt in self.AUDIO_INPUT_FORMATS]:
            target_format = self.audio_format_combo.currentText()
        else:
            return  # Unsupported format
        
        media_file = MediaFile(
            source_path=file_path,
            source_format=ext,
            target_format=target_format
        )
        
        self.media_files.append(media_file)
    
    def clear_files(self):
        """Clear all files"""
        self.media_files.clear()
        self.update_table()
    
    def update_table(self):
        """Update files table"""
        self.files_table.setRowCount(0)
        
        for media_file in self.media_files:
            row = self.files_table.rowCount()
            self.files_table.insertRow(row)
            
            self.files_table.setItem(row, 0, QTableWidgetItem(media_file.filename))
            self.files_table.setItem(row, 1, QTableWidgetItem(media_file.source_format.upper()))
            self.files_table.setItem(row, 2, QTableWidgetItem(media_file.target_format.upper()))
            self.files_table.setItem(row, 3, QTableWidgetItem(media_file.status))
        
        self.file_count_label.setText(f"Files: {len(self.media_files)}")
        self.convert_btn.setEnabled(len(self.media_files) > 0)
    
    def browse_output_dir(self):
        """Browse for output directory"""
        folder = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if folder:
            self.output_dir_input.setText(folder)
    
    def start_conversion(self):
        """Start conversion process"""
        if not self.media_files:
            return
        
        output_dir = self.output_dir_input.text()
        if not output_dir or not os.path.exists(output_dir):
            QMessageBox.warning(
                self,
                "Invalid Output Directory",
                "Please select a valid output directory."
            )
            return
        
        # Confirm if delete originals is checked
        if self.delete_originals_check.isChecked():
            reply = QMessageBox.question(
                self,
                "Confirm Deletion",
                "⚠️ You have selected to DELETE ORIGINAL FILES after conversion.\n\n"
                "This action CANNOT be undone!\n\n"
                "Are you absolutely sure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        # Get settings
        image_quality = self.image_quality_spin.value()
        
        video_quality_idx = self.video_quality_combo.currentIndex()
        video_quality = ['fast', 'medium', 'slow'][video_quality_idx]
        
        video_resolution = None
        if self.video_resolution_combo.currentIndex() > 0:
            video_resolution = self.video_resolution_combo.currentText().split(' ')[0]
        
        audio_bitrate = self.audio_bitrate_combo.currentText()
        
        audio_sample_rate = None
        if self.audio_sample_rate_combo.currentIndex() > 0:
            sample_text = self.audio_sample_rate_combo.currentText()
            audio_sample_rate = int(sample_text.split(' ')[0])
        
        delete_originals = self.delete_originals_check.isChecked()
        
        # Disable UI
        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.add_files_btn.setEnabled(False)
        self.add_folder_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        
        # Reset statuses
        for media_file in self.media_files:
            media_file.status = "Pending"
        self.update_table()
        
        # Start conversion thread
        self.conversion_thread = MediaConversionThread(
            self.media_files,
            output_dir,
            image_quality,
            video_quality,
            video_resolution,
            audio_bitrate,
            audio_sample_rate,
            delete_originals
        )
        
        self.conversion_thread.progress.connect(self.on_progress)
        self.conversion_thread.file_started.connect(self.on_file_started)
        self.conversion_thread.file_completed.connect(self.on_file_completed)
        self.conversion_thread.conversion_complete.connect(self.on_conversion_complete)
        self.conversion_thread.start()
    
    def cancel_conversion(self):
        """Cancel conversion"""
        if self.conversion_thread:
            self.conversion_thread.stop()
            self.conversion_thread.wait()
        
        self.reset_ui()
        self.status_label.setText("Conversion cancelled")
    
    def on_progress(self, current: int, total: int):
        """Update progress"""
        if total > 0:
            self.progress_bar.setValue(int((current / total) * 100))
    
    def on_file_started(self, filename: str):
        """Handle file conversion start"""
        # Find and update status
        for i, media_file in enumerate(self.media_files):
            if media_file.filename == filename:
                media_file.status = "Converting..."
                self.files_table.setItem(i, 3, QTableWidgetItem(media_file.status))
                break
        
        self.status_label.setText(f"Converting: {filename}")
    
    def on_file_completed(self, filename: str, success: bool, message: str):
        """Handle file conversion completion"""
        # Find and update status
        for i, media_file in enumerate(self.media_files):
            if media_file.filename == filename:
                if success:
                    media_file.status = "✓ Success"
                    media_file.error_message = ""
                else:
                    media_file.status = "✗ Failed"
                    media_file.error_message = message
                
                item = QTableWidgetItem(media_file.status)
                if success:
                    item.setForeground(QColor(0, 150, 0))
                else:
                    item.setForeground(QColor(200, 0, 0))
                    item.setToolTip(message)
                
                self.files_table.setItem(i, 3, item)
                break
    
    def on_conversion_complete(self, success: int, failed: int, skipped: int):
        """Handle conversion completion"""
        self.progress_bar.setValue(100)
        self.reset_ui()
        
        self.status_label.setText(f"Conversion complete: {success} succeeded, {failed} failed")
        
        QMessageBox.information(
            self,
            "Conversion Complete",
            f"<b>Conversion finished!</b><br><br>"
            f"✓ Successfully converted: {success}<br>"
            f"✗ Failed: {failed}<br>"
            f"➤ Skipped: {skipped}"
        )
    
    def reset_ui(self):
        """Reset UI after conversion"""
        self.convert_btn.setEnabled(len(self.media_files) > 0)
        self.cancel_btn.setEnabled(False)
        self.add_files_btn.setEnabled(True)
        self.add_folder_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)

# ============================================================================
# TAB 5 : FACE RECOGNITION 
# ============================================================================

def process_single_image_fast(image_path: str, reference_encodings: List[np.ndarray], 
                               similarity_threshold: float, max_dimension: int = 800) -> Optional[FaceMatch]:
    """
    Process a single image for face matching with multiple reference faces.
    Returns which specific face IDs matched.
    """
    try:
        image = face_recognition.load_image_file(image_path)
        
        height, width = image.shape[:2]
        if max(height, width) > max_dimension:
            scale = max_dimension / max(height, width)
            new_width = int(width * scale)
            new_height = int(height * scale)
            
            try:
                from PIL import Image as PILImage
                pil_image = PILImage.fromarray(image)
                pil_image = pil_image.resize((new_width, new_height), PILImage.Resampling.LANCZOS)
                image = np.array(pil_image)
            except:
                pass
        
        face_locations = face_recognition.face_locations(image, model='hog')
        
        if len(face_locations) == 0:
            return None
        
        face_encodings = face_recognition.face_encodings(image, face_locations)
        
        best_similarity = 0.0
        matched_face_ids = []
        
        for face_encoding in face_encodings:
            for face_id, ref_encoding in enumerate(reference_encodings):
                face_distance = face_recognition.face_distance(
                    [ref_encoding],
                    face_encoding
                )[0]
                
                similarity = 1.0 - min(face_distance, 1.0)
                
                if similarity >= similarity_threshold:
                    best_similarity = max(best_similarity, similarity)
                    if face_id not in matched_face_ids:
                        matched_face_ids.append(face_id)
        
        if matched_face_ids:
            return FaceMatch(
                image_path=image_path,
                similarity=best_similarity,
                face_locations=face_locations,
                matched_face_ids=matched_face_ids
            )
        
        return None
        
    except Exception as e:
        return None

def init_worker_process(ref_encodings_data):
    """Initialize worker process with shared reference encodings"""
    global _worker_reference_encodings
    _worker_reference_encodings = ref_encodings_data


def worker_process_image(image_path: str, similarity_threshold: float, 
                         max_dimension: int) -> Optional[FaceMatch]:
    """Worker function for multiprocessing pool"""
    global _worker_reference_encodings
    return process_single_image_fast(
        image_path, 
        _worker_reference_encodings, 
        similarity_threshold,
        max_dimension
    )


class OptimizedFaceRecognitionThread(QThread):
    """
    UPGRADED: Background thread for face recognition scanning with multi-face support.
    """
    
    progress = pyqtSignal(str)
    files_scanned = pyqtSignal(int, int)
    match_found = pyqtSignal(object)
    scan_complete = pyqtSignal(int, int)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, reference_encodings: List[np.ndarray], search_folder: str,
                 similarity_threshold: float = 0.6,
                 recursive: bool = True,
                 num_workers: Optional[int] = None,
                 max_image_dimension: int = 800,
                 parent=None):
        super().__init__(parent)
        self.reference_encodings = reference_encodings  # UPGRADED: Now accepts list
        self.search_folder = search_folder
        self.similarity_threshold = similarity_threshold
        self.recursive = recursive
        self.should_stop = False
        
        if num_workers is None:
            self.num_workers = max(1, cpu_count() - 1)
        else:
            self.num_workers = num_workers
        
        self.max_image_dimension = max_image_dimension
        self.supported_formats = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}
    
    def run(self):
        """Scan folder for faces matching reference encodings"""
        try:
            # Collect all image files
            self.progress.emit("Scanning for images...")
            image_files = []
            
            if self.recursive:
                for root, dirs, files in os.walk(self.search_folder):
                    if self.should_stop:
                        return
                    
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    
                    for filename in files:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in self.supported_formats:
                            image_files.append(os.path.join(root, filename))
            else:
                for item in os.listdir(self.search_folder):
                    filepath = os.path.join(self.search_folder, item)
                    if os.path.isfile(filepath):
                        ext = os.path.splitext(item)[1].lower()
                        if ext in self.supported_formats:
                            image_files.append(filepath)
            
            total_files = len(image_files)
            if total_files == 0:
                self.error_occurred.emit("No image files found in the selected folder.")
                return
            
            self.progress.emit(
                f"Found {total_files} images. "
                f"Starting parallel face recognition with {self.num_workers} workers..."
            )
            
            # Parallel processing
            matches_found = 0
            processed_count = 0
            
            process_func = partial(
                worker_process_image,
                similarity_threshold=self.similarity_threshold,
                max_dimension=self.max_image_dimension
            )
            
            try:
                with Pool(
                    processes=self.num_workers,
                    initializer=init_worker_process,
                    initargs=(self.reference_encodings,)  # UPGRADED: Pass all encodings
                ) as pool:
                    
                    chunk_size = 10
                    
                    for i in range(0, total_files, chunk_size):
                        if self.should_stop:
                            pool.terminate()
                            return
                        
                        chunk = image_files[i:i + chunk_size]
                        results = pool.map(process_func, chunk)
                        
                        for result in results:
                            processed_count += 1
                            
                            if result is not None:
                                matches_found += 1
                                self.match_found.emit(result)
                            
                            self.files_scanned.emit(processed_count, total_files)
                
            except Exception as e:
                self.error_occurred.emit(f"Processing error: {str(e)}")
                return
            
            if not self.should_stop:
                self.scan_complete.emit(matches_found, total_files)
        
        except Exception as e:
            self.error_occurred.emit(f"Unexpected error: {str(e)}")
    
    def stop(self):
        """Stop the scan"""
        self.should_stop = True

# FILE / CLASS TO UPDATE: FaceSearchTab class
# REPLACE WITH THE FOLLOWING CODE:

class FaceSearchTab(QWidget):
    """Face recognition search with multi-face detection and folder organization"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.reference_image_path: Optional[str] = None
        self.search_folder: Optional[str] = None
        self.matches: List[FaceMatch] = []
        self.recognition_thread: Optional[OptimizedFaceRecognitionThread] = None
        self.detected_faces: List[DetectedFace] = []
        
        self.init_ui()
        
        if not FACE_RECOGNITION_AVAILABLE:
            self.show_installation_warning()
    
    def init_ui(self):
        """Initialize UI with resizable sections"""
        # Main vertical splitter for the entire tab
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # ===== TOP SECTION: Configuration =====
        top_widget = QWidget()
        top_layout = QVBoxLayout()
        top_layout.setContentsMargins(5, 5, 5, 5)
        
        info = QLabel(
            "<b>Find a Person in Photos (100% Offline - UPGRADED)</b><br>"
            "Select a reference photo, preview and select faces, then scan a folder to find matches.<br>"
            f"<b>Multi-Face Support:</b> Detect and select specific faces | "
            f"<b>Performance:</b> Using {max(1, cpu_count() - 1)} CPU cores"
        )
        info.setStyleSheet(
            "padding: 10px; background-color: #e3f2fd; border: 1px solid #2196f3; "
            "border-radius: 5px; color: #1565c0;"
        )
        info.setWordWrap(True)
        top_layout.addWidget(info)
        
        # Reference image selection
        ref_group = QGroupBox("1. Select Reference Image & Choose Face(s)")
        ref_layout = QVBoxLayout()
        
        ref_buttons = QHBoxLayout()
        
        self.ref_image_btn = QPushButton("Select Reference Photo")
        self.ref_image_btn.clicked.connect(self.select_reference_image)
        self.ref_image_btn.setStyleSheet("background-color: #4caf50; font-weight: bold;")
        
        self.clear_ref_btn = QPushButton("Clear")
        self.clear_ref_btn.clicked.connect(self.clear_reference)
        self.clear_ref_btn.setEnabled(False)
        
        ref_buttons.addWidget(self.ref_image_btn)
        ref_buttons.addWidget(self.clear_ref_btn)
        ref_buttons.addStretch()
        
        ref_layout.addLayout(ref_buttons)
        
        self.ref_image_label = QLabel("No reference image selected")
        self.ref_image_label.setStyleSheet(
            "padding: 10px; background-color: #f5f5f5; border: 1px solid #ddd; "
            "border-radius: 3px; min-height: 60px;"
        )
        self.ref_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ref_image_label.setWordWrap(True)
        ref_layout.addWidget(self.ref_image_label)
        
        # IMPROVED: Face selection with better layout
        self.face_selection_group = QGroupBox("Detected Faces (Select to Match)")
        self.face_selection_group.setVisible(False)
        face_selection_layout = QVBoxLayout()
        
        # Scroll area for face grid with better sizing
        face_scroll = QScrollArea()
        face_scroll.setWidgetResizable(True)
        face_scroll.setMinimumHeight(200)
        face_scroll.setMaximumHeight(350)
        
        self.face_grid_widget = QWidget()
        self.face_grid_layout = QHBoxLayout()
        self.face_grid_layout.setSpacing(15)  # Increased spacing
        self.face_grid_layout.setContentsMargins(10, 10, 10, 10)
        self.face_grid_widget.setLayout(self.face_grid_layout)
        
        face_scroll.setWidget(self.face_grid_widget)
        face_selection_layout.addWidget(face_scroll)
        
        selection_controls = QHBoxLayout()
        
        self.select_all_faces_btn = QPushButton("Select All")
        self.select_all_faces_btn.clicked.connect(self.select_all_faces)
        
        self.deselect_all_faces_btn = QPushButton("Deselect All")
        self.deselect_all_faces_btn.clicked.connect(self.deselect_all_faces)
        
        self.face_count_label = QLabel("No faces selected")
        self.face_count_label.setStyleSheet("font-weight: bold;")
        
        selection_controls.addWidget(self.select_all_faces_btn)
        selection_controls.addWidget(self.deselect_all_faces_btn)
        selection_controls.addStretch()
        selection_controls.addWidget(self.face_count_label)
        
        face_selection_layout.addLayout(selection_controls)
        
        self.face_selection_group.setLayout(face_selection_layout)
        ref_layout.addWidget(self.face_selection_group)
        
        ref_group.setLayout(ref_layout)
        top_layout.addWidget(ref_group)
        
        # Folder selection
        folder_group = QGroupBox("2. Select Folder to Search")
        folder_layout = QVBoxLayout()
        
        folder_controls = QHBoxLayout()
        
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("No folder selected...")
        self.folder_input.setReadOnly(True)
        
        browse_folder_btn = QPushButton("Browse")
        browse_folder_btn.clicked.connect(self.select_search_folder)
        
        folder_controls.addWidget(self.folder_input, 1)
        folder_controls.addWidget(browse_folder_btn)
        
        folder_layout.addLayout(folder_controls)
        
        self.recursive_check = QCheckBox("Include subfolders")
        self.recursive_check.setChecked(True)
        folder_layout.addWidget(self.recursive_check)
        
        folder_group.setLayout(folder_layout)
        top_layout.addWidget(folder_group)
        
        # Settings
        settings_group = QGroupBox("3. Recognition Settings")
        settings_layout = QGridLayout()
        
        settings_layout.addWidget(QLabel("Similarity Threshold:"), 0, 0)
        
        threshold_container = QHBoxLayout()
        self.threshold_slider = QSpinBox()
        self.threshold_slider.setRange(40, 95)
        self.threshold_slider.setValue(60)
        self.threshold_slider.setSuffix("%")
        self.threshold_slider.setToolTip(
            "Minimum similarity to consider a match:\n"
            "40-50%: Very loose (many false positives)\n"
            "60-70%: Balanced (recommended)\n"
            "80-95%: Very strict (may miss some matches)"
        )
        threshold_container.addWidget(self.threshold_slider)
        threshold_container.addStretch()
        
        settings_layout.addLayout(threshold_container, 0, 1)
        
        settings_layout.addWidget(QLabel("Processing Speed:"), 1, 0)
        
        speed_container = QHBoxLayout()
        self.speed_combo = QComboBox()
        self.speed_combo.addItems([
            f"Fast (downsampled, {max(1, cpu_count() - 1)} cores)",
            "Balanced (moderate quality)",
            "Accurate (full resolution, slower)"
        ])
        self.speed_combo.setCurrentIndex(0)
        speed_container.addWidget(self.speed_combo)
        speed_container.addStretch()
        
        settings_layout.addLayout(speed_container, 1, 1)
        
        settings_group.setLayout(settings_layout)
        top_layout.addWidget(settings_group)
        
        # Action buttons
        action_layout = QHBoxLayout()
        
        self.search_btn = QPushButton("🔍 Start Face Search")
        self.search_btn.clicked.connect(self.start_face_search)
        self.search_btn.setEnabled(False)
        self.search_btn.setStyleSheet("background-color: #2196f3; font-weight: bold; font-size: 12pt;")
        
        self.cancel_btn = QPushButton("⏹ Cancel")
        self.cancel_btn.clicked.connect(self.cancel_search)
        self.cancel_btn.setEnabled(False)
        
        action_layout.addWidget(self.search_btn)
        action_layout.addWidget(self.cancel_btn)
        action_layout.addStretch()
        
        top_layout.addLayout(action_layout)
        
        top_widget.setLayout(top_layout)
        main_splitter.addWidget(top_widget)
        
        # ===== BOTTOM SECTION: Results (in a splitter) =====
        bottom_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Results table
        results_widget = QWidget()
        results_layout = QVBoxLayout()
        results_layout.setContentsMargins(5, 5, 5, 5)
        
        results_group = QGroupBox("Search Results")
        results_group_layout = QVBoxLayout()
        
        results_controls = QHBoxLayout()
        
        self.results_label = QLabel("No search performed yet")
        self.results_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        
        self.copy_btn = QPushButton("📁 Copy Matches to Organized Folders")
        self.copy_btn.clicked.connect(self.copy_matches_organized)
        self.copy_btn.setEnabled(False)
        
        self.clear_results_btn = QPushButton("Clear Results")
        self.clear_results_btn.clicked.connect(self.clear_results)
        self.clear_results_btn.setEnabled(False)
        
        results_controls.addWidget(self.results_label)
        results_controls.addStretch()
        results_controls.addWidget(self.copy_btn)
        results_controls.addWidget(self.clear_results_btn)
        
        results_group_layout.addLayout(results_controls)
        
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(4)
        self.results_table.setHorizontalHeaderLabels(['Filename', 'Similarity', 'Matched Faces', 'Full Path'])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.doubleClicked.connect(self.open_matched_image)
        
        results_group_layout.addWidget(self.results_table)
        
        results_group.setLayout(results_group_layout)
        results_layout.addWidget(results_group)
        
        results_widget.setLayout(results_layout)
        bottom_splitter.addWidget(results_widget)
        
        # Progress section
        progress_widget = QWidget()
        progress_layout = QVBoxLayout()
        progress_layout.setContentsMargins(5, 5, 5, 5)
        
        progress_group = QGroupBox("Progress")
        progress_group_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        
        self.speed_label = QLabel("")
        self.speed_label.setStyleSheet("color: #666; font-size: 9pt;")
        
        progress_group_layout.addWidget(self.progress_bar)
        progress_group_layout.addWidget(self.status_label)
        progress_group_layout.addWidget(self.speed_label)
        
        progress_group.setLayout(progress_group_layout)
        progress_layout.addWidget(progress_group)
        
        progress_widget.setLayout(progress_layout)
        bottom_splitter.addWidget(progress_widget)
        
        # Set initial sizes for bottom splitter (results take more space)
        bottom_splitter.setSizes([600, 150])
        
        main_splitter.addWidget(bottom_splitter)
        
        # Set initial sizes for main splitter (top and bottom sections)
        main_splitter.setSizes([400, 400])
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)  # Allow content to resize
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)  # Remove frame for cleaner look
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Set the main splitter as the scroll area's widget
        scroll_area.setWidget(main_splitter)
        
        # Set the scroll area as the main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)
        
        self.setLayout(main_layout)
        
        self.scan_start_time = None
        self.last_update_time = None
        self.last_processed_count = 0
        
    def show_installation_warning(self):
        """Show warning if face_recognition is not installed"""
        QMessageBox.warning(
            self,
            "Missing Dependency",
            "<b>Face Recognition library not installed</b><br><br>"
            "This feature requires the 'face_recognition' library.<br><br>"
            "<b>Installation:</b><br>"
            "1. Open terminal/command prompt<br>"
            "2. Run: <code>pip install face_recognition</code><br><br>"
            "After installation, restart the application."
        )
        
        self.ref_image_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
    
    def select_reference_image(self):
        """Select reference image and detect all faces"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Reference Photo",
            "",
            "Images (*.jpg *.jpeg *.png *.bmp *.gif);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        self.reference_image_path = file_path
        self.ref_image_label.setText(f"⚙️ Processing: {os.path.basename(file_path)}...")
        self.ref_image_label.setStyleSheet(
            "padding: 10px; background-color: #fff3cd; border: 1px solid #ffc107; "
            "border-radius: 3px; min-height: 80px; color: #856404;"
        )
        
        QApplication.processEvents()
        self.detect_faces_in_reference()
    
    def detect_faces_in_reference(self):
        """Detect all faces in reference image and create thumbnails"""
        try:
            image = face_recognition.load_image_file(self.reference_image_path)
            
            face_locations = face_recognition.face_locations(image, model='hog')
            
            if len(face_locations) == 0:
                QMessageBox.warning(
                    self,
                    "No Faces Detected",
                    "No faces were detected in the reference image.\n\n"
                    "Please select an image with at least one clear, visible face."
                )
                self.clear_reference()
                return
            
            face_encodings = face_recognition.face_encodings(image, face_locations)
            
            self.detected_faces.clear()
            
            for i, (encoding, location) in enumerate(zip(face_encodings, face_locations)):
                top, right, bottom, left = location
                
                padding = 20
                top_pad = max(0, top - padding)
                right_pad = min(image.shape[1], right + padding)
                bottom_pad = min(image.shape[0], bottom + padding)
                left_pad = max(0, left - padding)
                
                face_thumbnail = image[top_pad:bottom_pad, left_pad:right_pad]
                
                try:
                    from PIL import Image as PILImage
                    pil_thumb = PILImage.fromarray(face_thumbnail)
                    pil_thumb = pil_thumb.resize((100, 100), PILImage.Resampling.LANCZOS)
                    face_thumbnail = np.array(pil_thumb)
                except:
                    pass
                
                detected_face = DetectedFace(
                    face_id=i + 1,
                    encoding=encoding,
                    location=location,
                    thumbnail=face_thumbnail,
                    selected=(i == 0)
                )
                
                self.detected_faces.append(detected_face)
            
            self.display_detected_faces()
            
            self.ref_image_label.setText(
                f"Reference image loaded:\n{os.path.basename(self.reference_image_path)}\n"
                f"Detected {len(self.detected_faces)} face(s)"
            )
            self.ref_image_label.setStyleSheet(
                "padding: 10px; background-color: #e8f5e9; border: 1px solid #4caf50; "
                "border-radius: 3px; min-height: 80px; color: #2e7d32;"
            )
            
            self.clear_ref_btn.setEnabled(True)
            self.update_search_button()
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to process reference image:\n{str(e)}"
            )
            self.clear_reference()
    
    def display_detected_faces(self):
        """Display detected face thumbnails with selection checkboxes"""
        while self.face_grid_layout.count():
            item = self.face_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        for face in self.detected_faces:
            face_widget = self.create_face_widget(face)
            self.face_grid_layout.addWidget(face_widget)
        
        self.face_grid_layout.addStretch()
        
        self.face_selection_group.setVisible(True)
        
        self.update_face_selection_count()
    
    def create_face_widget(self, face: DetectedFace) -> QWidget:
        """Create a widget for a single detected face with improved layout"""
        widget = QWidget()
        widget.setStyleSheet(
            "QWidget { background-color: #f8f9fa; border: 2px solid #dee2e6; "
            "border-radius: 8px; padding: 10px; }"
        )
        widget.setFixedWidth(180)  # Fixed width for consistent layout
        
        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # IMPROVED: Larger, clearer thumbnail
        thumbnail_label = QLabel()
        thumbnail_label.setFixedSize(150, 150)  # Larger preview
        thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail_label.setStyleSheet(
            "QLabel { border: 2px solid #adb5bd; border-radius: 5px; "
            "background-color: #ffffff; }"
        )
        
        if face.thumbnail is not None:
            try:
                from PIL import Image as PILImage
                from PyQt6.QtGui import QPixmap, QImage
                
                # Resize thumbnail to exactly fit the label
                height, width, channel = face.thumbnail.shape
                bytes_per_line = 3 * width
                q_image = QImage(
                    face.thumbnail.data, 
                    width, 
                    height, 
                    bytes_per_line, 
                    QImage.Format.Format_RGB888
                )
                pixmap = QPixmap.fromImage(q_image)
                
                # Scale to fit while maintaining aspect ratio
                scaled_pixmap = pixmap.scaled(
                    150, 150,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                thumbnail_label.setPixmap(scaled_pixmap)
            except:
                thumbnail_label.setText(f"Face {face.face_id}")
                thumbnail_label.setStyleSheet(
                    "QLabel { border: 2px solid #adb5bd; border-radius: 5px; "
                    "background-color: #e9ecef; font-weight: bold; }"
                )
        else:
            thumbnail_label.setText(f"Face {face.face_id}")
            thumbnail_label.setStyleSheet(
                "QLabel { border: 2px solid #adb5bd; border-radius: 5px; "
                "background-color: #e9ecef; font-weight: bold; }"
            )
        
        layout.addWidget(thumbnail_label, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # IMPROVED: Larger, more visible checkbox
        checkbox = QCheckBox(f"Face {face.face_id}")
        checkbox.setChecked(face.selected)
        checkbox.setStyleSheet(
            "QCheckBox { font-weight: bold; font-size: 11pt; }"
            "QCheckBox::indicator { width: 20px; height: 20px; }"
        )
        checkbox.stateChanged.connect(
            lambda state, f=face: self.on_face_selection_changed(f, state)
        )
        
        layout.addWidget(checkbox, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Location info (smaller, less prominent)
        info_label = QLabel(face.get_location_string())
        info_label.setStyleSheet("font-size: 8pt; color: #6c757d;")
        info_label.setWordWrap(True)
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)
        
        widget.setLayout(layout)
        return widget
       
    def on_face_selection_changed(self, face: DetectedFace, state: int):
        """Handle face selection change"""
        face.selected = (state == Qt.CheckState.Checked.value)
        self.update_face_selection_count()
        self.update_search_button()
    
    def update_face_selection_count(self):
        """Update face selection count label"""
        selected_count = sum(1 for f in self.detected_faces if f.selected)
        total_count = len(self.detected_faces)
        
        self.face_count_label.setText(
            f"Selected: {selected_count} of {total_count} face(s)"
        )
    
    def select_all_faces(self):
        """Select all detected faces"""
        for face in self.detected_faces:
            face.selected = True
        self.display_detected_faces()
        self.update_search_button()
    
    def deselect_all_faces(self):
        """Deselect all detected faces"""
        for face in self.detected_faces:
            face.selected = False
        self.display_detected_faces()
        self.update_search_button()
    
    def clear_reference(self):
        """Clear reference image and detected faces"""
        self.reference_image_path = None
        self.detected_faces.clear()
        
        self.ref_image_label.setText("No reference image selected")
        self.ref_image_label.setStyleSheet(
            "padding: 10px; background-color: #f5f5f5; border: 1px solid #ddd; "
            "border-radius: 3px; min-height: 80px;"
        )
        
        self.face_selection_group.setVisible(False)
        self.clear_ref_btn.setEnabled(False)
        self.update_search_button()
    
    def select_search_folder(self):
        """Select folder to search"""
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Search")
        
        if folder:
            self.search_folder = folder
            self.folder_input.setText(folder)
            self.update_search_button()
    
    def update_search_button(self):
        """Enable search button only if faces are selected"""
        selected_faces = [f for f in self.detected_faces if f.selected]
        
        self.search_btn.setEnabled(
            self.reference_image_path is not None and
            self.search_folder is not None and
            len(selected_faces) > 0 and
            FACE_RECOGNITION_AVAILABLE
        )
    
    def start_face_search(self):
        """Start face recognition search with selected faces"""
        if not self.reference_image_path or not self.search_folder:
            return
        
        selected_faces = [f for f in self.detected_faces if f.selected]
        
        if len(selected_faces) == 0:
            QMessageBox.warning(
                self,
                "No Faces Selected",
                "Please select at least one face to match."
            )
            return
        
        reference_encodings = [f.encoding for f in selected_faces]
        
        self.clear_results()
        
        threshold = self.threshold_slider.value() / 100.0
        recursive = self.recursive_check.isChecked()
        
        speed_idx = self.speed_combo.currentIndex()
        if speed_idx == 0:
            max_dimension = 800
            num_workers = max(1, cpu_count() - 1)
        elif speed_idx == 1:
            max_dimension = 1200
            num_workers = max(1, cpu_count() // 2)
        else:
            max_dimension = 2400
            num_workers = max(1, cpu_count() // 2)
        
        self.search_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.ref_image_btn.setEnabled(False)
        self.clear_ref_btn.setEnabled(False)
        
        self.progress_bar.setValue(0)
        
        face_list = ", ".join([f"Face {f.face_id}" for f in selected_faces])
        self.status_label.setText(
            f"Starting search for {len(selected_faces)} face(s): {face_list}"
        )
        self.speed_label.setText("")
        
        self.scan_start_time = time.time()
        self.last_update_time = time.time()
        self.last_processed_count = 0
        
        self.recognition_thread = OptimizedFaceRecognitionThread(
            reference_encodings,
            self.search_folder,
            threshold,
            recursive,
            num_workers=num_workers,
            max_image_dimension=max_dimension
        )
        
        self.recognition_thread.progress.connect(self.on_progress)
        self.recognition_thread.files_scanned.connect(self.on_files_scanned)
        self.recognition_thread.match_found.connect(self.on_match_found)
        self.recognition_thread.scan_complete.connect(self.on_scan_complete)
        self.recognition_thread.error_occurred.connect(self.on_error)
        self.recognition_thread.start()
    
    def cancel_search(self):
        """Cancel face search"""
        if self.recognition_thread:
            self.recognition_thread.stop()
            self.recognition_thread.wait()
        
        self.reset_ui()
        self.status_label.setText("Search cancelled")
        self.speed_label.setText("")
    
    def on_progress(self, message: str):
        """Update progress message"""
        self.status_label.setText(message)
    
    def on_files_scanned(self, current: int, total: int):
        """Update scan progress with speed indicator"""
        if total > 0:
            progress = int((current / total) * 100)
            self.progress_bar.setValue(progress)
            
            current_time = time.time()
            if self.last_update_time and current_time - self.last_update_time > 0.5:
                elapsed = current_time - self.last_update_time
                processed = current - self.last_processed_count
                
                if processed > 0:
                    speed = processed / elapsed
                    eta_seconds = (total - current) / speed if speed > 0 else 0
                    
                    self.speed_label.setText(
                        f"Speed: {speed:.1f} images/sec | "
                        f"ETA: {int(eta_seconds)}s | "
                        f"Processed: {current}/{total}"
                    )
                    
                    self.last_update_time = current_time
                    self.last_processed_count = current
            
            self.status_label.setText(
                f"Scanning images: {current}/{total} ({progress}%)"
            )
    
    def on_match_found(self, match: FaceMatch):
        """Handle new match found"""
        self.matches.append(match)
        
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        
        self.results_table.setItem(row, 0, QTableWidgetItem(match.filename))
        
        similarity_item = QTableWidgetItem(match.similarity_percent)
        similarity_item.setForeground(QColor(0, 150, 0))
        self.results_table.setItem(row, 1, similarity_item)
        
        matched_faces_str = ", ".join([f"Face {fid+1}" for fid in match.matched_face_ids])
        self.results_table.setItem(row, 2, QTableWidgetItem(matched_faces_str))
        
        self.results_table.setItem(row, 3, QTableWidgetItem(match.image_path))
        
        self.results_label.setText(f"Found {len(self.matches)} matches")
    
    def on_scan_complete(self, matches_found: int, total_scanned: int):
        """Handle scan completion"""
        self.progress_bar.setValue(100)
        self.reset_ui()
        
        if self.scan_start_time:
            total_time = time.time() - self.scan_start_time
            avg_speed = total_scanned / total_time if total_time > 0 else 0
            
            self.speed_label.setText(
                f"Completed in {total_time:.1f}s | "
                f"Average speed: {avg_speed:.1f} images/sec"
            )
        
        self.matches.sort()
        self.update_results_table()
        
        selected_count = sum(1 for f in self.detected_faces if f.selected)
        
        self.status_label.setText(
            f"Search complete: Found {matches_found} matches for {selected_count} face(s) "
            f"in {total_scanned} images"
        )
        
        self.results_label.setText(
            f"Found {matches_found} matches out of {total_scanned} images scanned"
        )
        
        if matches_found > 0:
            self.copy_btn.setEnabled(True)
            self.clear_results_btn.setEnabled(True)
            
            QMessageBox.information(
                self,
                "Search Complete",
                f"Face recognition complete!\n\n"
                f"Searched for: {selected_count} face(s)\n"
                f"Found: {matches_found} matching images\n"
                f"Total scanned: {total_scanned}\n"
                f"Time: {total_time:.1f} seconds" if self.scan_start_time else ""
            )
        else:
            QMessageBox.information(
                self,
                "No Matches",
                f"No matching faces found in {total_scanned} images.\n\n"
                "Try:\n"
                "• Lowering the similarity threshold\n"
                "• Using a clearer reference photo\n"
                "• Selecting different faces\n"
                "• Ensuring the person appears in the searched photos"
            )
    
    def on_error(self, error: str):
        """Handle error"""
        self.reset_ui()
        self.progress_bar.setValue(0)
        self.status_label.setText(f"Error: {error}")
        self.speed_label.setText("")
        QMessageBox.critical(self, "Error", error)
    
    def update_results_table(self):
        """Update results table with sorted matches"""
        self.results_table.setRowCount(0)
        
        for match in self.matches:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            
            self.results_table.setItem(row, 0, QTableWidgetItem(match.filename))
            
            similarity_item = QTableWidgetItem(match.similarity_percent)
            similarity_item.setForeground(QColor(0, 150, 0))
            self.results_table.setItem(row, 1, similarity_item)
            
            matched_faces_str = ", ".join([f"Face {fid+1}" for fid in match.matched_face_ids])
            self.results_table.setItem(row, 2, QTableWidgetItem(matched_faces_str))
            
            self.results_table.setItem(row, 3, QTableWidgetItem(match.image_path))
    
    def open_matched_image(self, index: QModelIndex):
        """Open matched image on double-click"""
        row = index.row()
        if row < len(self.matches):
            image_path = self.matches[row].image_path
            
            try:
                if sys.platform == 'win32':
                    os.startfile(image_path)
                elif sys.platform == 'darwin':
                    os.system(f'open "{image_path}"')
                else:
                    os.system(f'xdg-open "{image_path}"')
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Cannot open image:\n{str(e)}")
    
    def copy_matches_organized(self):
        """Copy matched images to organized folders"""
        if not self.matches:
            return
        
        output_folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory for Organized Folders"
        )
        
        if not output_folder:
            return
        
        try:
            self.status_label.setText("Organizing results into folders...")
            QApplication.processEvents()
            
            selected_faces = [f for f in self.detected_faces if f.selected]
            face_folders = {}
            for face in selected_faces:
                folder_name = f"Face_{face.face_id}"
                folder_path = os.path.join(output_folder, folder_name)
                os.makedirs(folder_path, exist_ok=True)
                face_folders[face.face_id - 1] = folder_path
            
            if len(selected_faces) > 1:
                all_together_path = os.path.join(output_folder, "All_Together")
                os.makedirs(all_together_path, exist_ok=True)
            
            copied = 0
            failed = 0
            
            for match in self.matches:
                try:
                    source = match.image_path
                    filename = os.path.basename(source)
                    
                    for face_id in match.matched_face_ids:
                        if face_id in face_folders:
                            dest_folder = face_folders[face_id]
                            dest = os.path.join(dest_folder, filename)
                            
                            counter = 1
                            base_name, ext = os.path.splitext(filename)
                            while os.path.exists(dest):
                                new_name = f"{base_name}_{counter}{ext}"
                                dest = os.path.join(dest_folder, new_name)
                                counter += 1
                            
                            shutil.copy2(source, dest)
                    
                    if len(selected_faces) > 1 and len(match.matched_face_ids) == len(selected_faces):
                        dest = os.path.join(all_together_path, filename)
                        
                        counter = 1
                        base_name, ext = os.path.splitext(filename)
                        while os.path.exists(dest):
                            new_name = f"{base_name}_{counter}{ext}"
                            dest = os.path.join(all_together_path, new_name)
                            counter += 1
                        
                        shutil.copy2(source, dest)
                    
                    copied += 1
                    
                except Exception:
                    failed += 1
            
            self.status_label.setText("Organization complete!")
            
            summary = f"Successfully organized: {copied} images\nFailed: {failed} images\n\n"
            summary += f"Output location: {output_folder}\n\n"
            summary += "Folders created:\n"
            for face in selected_faces:
                folder_name = f"Face_{face.face_id}"
                summary += f"• {folder_name}: Individual matches\n"
            if len(selected_faces) > 1:
                summary += "• All_Together: Images with all selected faces"
            
            QMessageBox.information(
                self,
                "Organization Complete",
                summary
            )
        
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to organize images:\n{str(e)}")

    def clear_results(self):
        """Clear search results"""
        self.matches.clear()
        self.results_table.setRowCount(0)
        self.results_label.setText("No search performed yet")
        self.copy_btn.setEnabled(False)
        self.clear_results_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.speed_label.setText("")
        self.scan_start_time = None
        self.last_update_time = None
        self.last_processed_count = 0

    def reset_ui(self):
        """Reset UI after search"""
        self.search_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.ref_image_btn.setEnabled(True)
        if self.reference_image_path:
            self.clear_ref_btn.setEnabled(True)

# ============================================================================
# TAB 6: HELP
# ============================================================================

class HelpTab(QWidget):
    """Help documentation with bilingual support"""

    def __init__(self, translation_manager: TranslationManager, parent=None):
        super().__init__(parent)
        self.translation_manager = translation_manager
        self.init_ui()

    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()

        controls_layout = QHBoxLayout()
        
        self.language_toggle = QComboBox()
        self.language_toggle.addItems(['English', 'فارسی'])
        self.language_toggle.currentIndexChanged.connect(self.update_help_content)
        
        self.donate_btn = QPushButton("❤️ Donate / حمایت مالی")
        self.donate_btn.setStyleSheet(
            "background-color: #e91e63; color: white; font-weight: bold; "
            "padding: 10px 20px; font-size: 12pt;"
        )
        self.donate_btn.clicked.connect(self.show_donate_dialog)
        
        controls_layout.addWidget(QLabel("Help Language:"))
        controls_layout.addWidget(self.language_toggle)
        controls_layout.addStretch()
        controls_layout.addWidget(self.donate_btn)
        
        layout.addLayout(controls_layout)

        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        
        layout.addWidget(self.help_text)
        self.setLayout(layout)
        
        self.update_help_content()

    def update_help_content(self):
        """Update help content based on selected language"""
        lang_index = self.language_toggle.currentIndex()
        
        if lang_index == 0:
            self.help_text.setHtml(self.get_help_content_english())
        else:
            self.help_text.setHtml(self.get_help_content_persian())

    def show_donate_dialog(self):
        """Show donation information dialog"""
        lang_index = self.language_toggle.currentIndex()
        
        if lang_index == 0:
            title = "Support Development"
            message = """
<html>
<body style="font-family: Arial; font-size: 11pt;">
    <h2 style="color: #e91e63;">❤️ Thank You for Your Support!</h2>
    
    <p>If you find this application useful, please consider supporting its development.</p>
    
    <h3>Donation Options:</h3>
    <ul>
        <li><b>PayPal:</b> I dont have it :) </li>
        <li><b>Donate Link:</b> <a></a></li>
    </ul>
    
    <p style="margin-top: 20px;">
        Your support helps maintain and improve this application.<br>
        Every contribution, no matter how small, is greatly appreciated!
    </p>
    
    <p style="margin-top: 20px; color: #666;">
        <b>Created by:</b> Ali Doulabi<br>
        <b>Email:</b> ali.doulabi.81@gmail.com
    </p>
</body>
</html>
"""
        else:
            title = "حمایت از توسعه"
            message = """
<html>
<body style="font-family: Tahoma; font-size: 11pt; direction: rtl; text-align: right;">
    <h2 style="color: #e91e63;">❤️ از حمایت شما سپاسگزاریم!</h2>
    
    <p>اگر این برنامه برای شما مفید است، لطفاً از توسعه آن حمایت کنید.</p>
    
    <h3>گزینه‌های کمک مالی:</h3>
    <ul style="text-align: right;">
        <li><b>پی‌پال:</b> ندارم </li>
        <li><b>لینک حمایت :</b> <a href="https://daramet.com/Ali_Dlb404">پلتفرم دارمت</a></li>
    </ul>
    
    <p style="margin-top: 20px;">
        حمایت شما به نگهداری و بهبود این برنامه کمک می‌کند.<br>
        هر کمکی، هر چقدر هم کوچک، بسیار ارزشمند است!
    </p>
    
    <p style="margin-top: 20px; color: #666;">
        <b>ساخته شده توسط:</b> علی دولابی<br>
        <b>ایمیل:</b> ali.doulabi.81@gmail.com
    </p>
</body>
</html>
"""
        
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setTextFormat(Qt.TextFormat.RichText)
        msg_box.setText(message)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()

    def get_help_content_english(self) -> str:
        """Get English help content"""
        return """
        <html>
        <body style="font-family: Arial, sans-serif;">
            <h1 style="color: #2c3e50;">FileScope - User Guide</h1>

            <h2 style="color: #3498db;">Tab 1: File Explorer with Auto-Indexing</h2>
            <h3>How It Works</h3>
            <p>When you start the application, the system <b>automatically indexes all files</b> on your computer in the background. This allows for <b>instant search results</b> without rescanning each time.</p>

            <h3>Features</h3>
            <ul>
                <li><b>Automatic System-Wide Indexing:</b> All drives are indexed at startup</li>
                <li><b>Instant Search:</b> Search results appear as you type</li>
                <li><b>Background Operation:</b> Indexing never blocks the UI</li>
                <li><b>Folder Filtering:</b> Optionally limit results to specific folders</li>
                <li><b>Sortable Columns:</b> Sort by name, size, date, etc.</li>
                <li><b>Quick Access:</b> Double-click to open files</li>
            </ul>

            <h3>Usage</h3>
            <ol>
                <li>Wait for initial indexing to complete (shown in status bar)</li>
                <li>Type in the search box to find files instantly</li>
                <li>Optionally add folder filter to narrow results</li>
                <li>Click column headers to sort</li>
                <li>Double-click any file to open it</li>
            </ol>

            <hr>

            <h2 style="color: #3498db;">Tab 2: File Organizer</h2>
            <p>Bulk move files by extension. Select file types, choose source and destination, and organize your files automatically.</p>

            <h3>How to Use</h3>
            <ol>
                <li>Select file extensions you want to organize</li>
                <li>Choose source folder</li>
                <li>Choose destination folder</li>
                <li>Click "Move Files" to organize</li>
            </ol>

            <hr>

            <h2 style="color: #3498db;">Tab 3: Intelligent Duplicate Finder</h2>
            
            <h3>⚡ Fast Scan Mode (Recommended)</h3>
            <p><b>How it works:</b> Uses intelligent filename normalization to detect common copy patterns.</p>
            
            <h4>What it detects:</h4>
            <ul>
                <li>Files like "document.pdf" and "document (1).pdf"</li>
                <li>Files like "photo.jpg" and "photo - Copy.jpg"</li>
                <li>Files like "video.mp4" and "video_2.mp4"</li>
            </ul>

            <h4>Options:</h4>
            <ul>
                <li><b>Match file size:</b> Only group files with identical sizes (more accurate)</li>
            </ul>

            <h4>Performance:</h4>
            <ul>
                <li>Speed: Very Fast (100-1000 files/second)</li>
                <li>Accuracy: Good for typical duplicate scenarios</li>
                <li>Best for: Quick cleanup of downloaded/copied files</li>
            </ul>

            <h3>🔬 Deep Scan Mode (Content-Based)</h3>
            <p><b>How it works:</b> Analyzes the actual binary content of each file using cryptographic hashing.</p>

            <h4>What it detects:</h4>
            <ul>
                <li>Files with identical content even if names are completely different</li>
                <li>Files that were renamed or moved</li>
                <li>Exact duplicate backups</li>
            </ul>

            <h4>Hash Algorithms:</h4>
            <ul>
                <li><b>MD5:</b> Fastest, recommended for most users</li>
                <li><b>SHA-1:</b> Balanced speed and security</li>
                <li><b>SHA-256:</b> Most secure, slowest</li>
            </ul>

            <p><i>All algorithms provide 100% accuracy for duplicate detection.</i></p>

            <h3>⚠️ Important Warnings</h3>
            <ul style="color: #e74c3c;">
                <li><b>Deletion is permanent</b> - files are not moved to recycle bin</li>
                <li><b>Review carefully</b> - some duplicates may be intentional</li>
                <li><b>Check file paths</b> - ensure you're deleting from the right location</li>
                <li><b>Keep the newest or largest</b> when in doubt</li>
            </ul>

            <hr>

            <h2 style="color: #3498db;">Tab 4: Media Format Converter</h2>
            
            <h3>⚠️ Important Information</h3>
            <p style="color: #d32f2f; font-weight: bold;">
                This is REAL media conversion - files are decoded and re-encoded, NOT just renamed!
            </p>
            
            <h3>Requirements</h3>
            <ul>
                <li><b>Pillow:</b> Required for image conversion (install: <code>pip install Pillow</code>)</li>
                <li><b>ffmpeg:</b> Required for video/audio conversion (download from ffmpeg.org)</li>
            </ul>
            
            <h3>Supported Formats</h3>
            <h4>Images</h4>
            <ul>
                <li><b>Input:</b> JPG, PNG, BMP, WebP, TIFF</li>
                <li><b>Output:</b> JPG, PNG, WebP</li>
            </ul>
            
            <h4>Videos</h4>
            <ul>
                <li><b>Input:</b> MP4, MKV, AVI, MOV, WebM</li>
                <li><b>Output:</b> MP4, MKV, WebM</li>
            </ul>
            
            <h4>Audio</h4>
            <ul>
                <li><b>Input:</b> MP3, WAV, AAC, FLAC, OGG</li>
                <li><b>Output:</b> MP3, WAV, AAC, FLAC</li>
            </ul>

            <hr>

            <h2 style="color: #3498db;">Tab 5: Face Search (100% Offline)</h2>

            <h3>Powerful Offline Face Recognition</h3>
            <p>Find all photos containing a specific person using deep learning face recognition - 
            completely offline, no internet required!</p>

            <h3>Requirements</h3>
            <ul>
                <li><b>face_recognition library:</b> Install with <code>pip install face_recognition</code></li>
                <li><b>First-time setup:</b> Models are downloaded once and cached locally</li>
                <li><b>After installation:</b> Works 100% offline</li>
            </ul>

            <h3>How It Works</h3>
            <ol>
                <li><b>Select Reference Photo:</b> Choose a clear photo of the person</li>
                <li><b>Select Faces:</b> Choose which face(s) to match from detected faces</li>
                <li><b>Select Search Folder:</b> Choose folder containing photos to search</li>
                <li><b>Adjust Similarity:</b> Set threshold (60-70% recommended)</li>
                <li><b>Start Search:</b> The app scans all images and finds matches</li>
                <li><b>Organize Results:</b> Copy matches to organized folders</li>
            </ol>

            <h3>NEW: Multi-Face Support & Organized Output</h3>
            <ul>
                <li><b>Multiple Faces:</b> Select multiple people to search for simultaneously</li>
                <li><b>Individual Folders:</b> Each person gets their own folder</li>
                <li><b>All Together Folder:</b> Images where ALL selected people appear together</li>
            </ul>

            <h3>Best Practices for Reference Photos</h3>
            <ul>
                <li>✓ Use a clear, well-lit photo</li>
                <li>✓ Face should be clearly visible and not obscured</li>
                <li>✓ Front-facing photos work best</li>
                <li>✓ Avoid sunglasses, heavy shadows, or extreme angles</li>
            </ul>

            <hr>

            <h2 style="color: #3498db;">Tab 6: Settings</h2>

            <h3>Available Settings</h3>
            <ul>
                <li><b>Language:</b> Switch between English and Persian (فارسی)</li>
                <li><b>Theme:</b> Choose Light, Dark, or Blue theme</li>
                <li><b>Font:</b> Customize font family and size</li>
                <li><b>Background Processing:</b> Enable/disable automatic file indexing</li>
            </ul>

            <h3>How to Change Settings</h3>
            <ol>
                <li>Go to Settings tab</li>
                <li>Adjust your preferred settings</li>
                <li>Click "Apply Settings"</li>
                <li>Some changes may require application restart</li>
            </ol>

            <hr>

            <h2 style="color: #3498db;">Keyboard Shortcuts</h2>
            <ul>
                <li><b>Ctrl+F:</b> Focus search (Explorer tab)</li>
                <li><b>F5:</b> Refresh</li>
                <li><b>Escape:</b> Clear/Cancel</li>
            </ul>

            <h2 style="color: #3498db;">Performance Tips</h2>
            <ul>
                <li>Initial indexing may take 5-15 minutes for large drives</li>
                <li>Search is instant after indexing completes</li>
                <li>Deep Scan can take minutes to hours depending on data size</li>
                <li>Face recognition: ~1-3 seconds per image</li>
                <li>Close other applications during intensive operations</li>
            </ul>

            <hr>

            <div style="margin-top: 40px; padding: 20px; background-color: #1f2933; border-left: 4px solid #3498db; border-radius: 5px;">
                <h2 style="color: ##969696; margin-top: 0;">About</h2>
                <p style="font-size: 12pt; margin: 10px 0;">
                    <b>Application Name:</b> FileScope<br>
                    <b>Version:</b> 1.0<br>
                    <b>Created by:</b> <span style="color: #3498db; font-weight: bold;">Ali Doulabi</span><br>
                    <b>Platform:</b> Windows, macOS, Linux<br>
                    <b>Technology:</b> Python, PyQt6
                </p>
                <p style="margin-top: 15px; font-style: italic; color: #666;">
                    If you find this application useful, please consider supporting its development 
                    by clicking the Donate button above. Your support helps maintain and improve this application!
                </p>
            </div>
            <hr>
            
        </body>
        </html>
        """

    def get_help_content_persian(self) -> str:
        """Get Persian help content"""
        return """
        <html>
        <body style="font-family: Tahoma, Arial; direction: rtl; text-align: right;">
            <h1 style="color: #2c3e50;">راهنمای جامع - مدیریت پیشرفته فایل‌ها</h1>

            <h2 style="color: #3498db;">بخش ۱: مرورگر فایل با نمایه‌سازی خودکار</h2>
            <h3>نحوه کار</h3>
            <p>هنگام راه‌اندازی برنامه، سیستم به‌طور <b>خودکار تمام فایل‌های</b> کامپیوتر شما را در پس‌زمینه نمایه‌سازی می‌کند. این امکان <b>جستجوی فوری</b> را بدون نیاز به اسکن مجدد فراهم می‌کند.</p>

            <h3>ویژگی‌ها</h3>
            <ul style="text-align: right;">
                <li><b>نمایه‌سازی خودکار سیستم:</b> تمام درایوها در هنگام راه‌اندازی نمایه‌سازی می‌شوند</li>
                <li><b>جستجوی فوری:</b> نتایج جستجو همزمان با تایپ نمایش داده می‌شوند</li>
                <li><b>عملکرد پس‌زمینه:</b> نمایه‌سازی هرگز رابط کاربری را مسدود نمی‌کند</li>
                <li><b>فیلتر پوشه:</b> محدود کردن نتایج به پوشه‌های خاص</li>
                <li><b>ستون‌های قابل مرتب‌سازی:</b> مرتب‌سازی بر اساس نام، اندازه، تاریخ و غیره</li>
                <li><b>دسترسی سریع:</b> دابل کلیک برای باز کردن فایل‌ها</li>
            </ul>

            <h3>نحوه استفاده</h3>
            <ol style="text-align: right;">
                <li>منتظر تکمیل نمایه‌سازی اولیه بمانید (در نوار وضعیت نمایش داده می‌شود)</li>
                <li>در کادر جستجو تایپ کنید تا فایل‌ها را فوراً پیدا کنید</li>
                <li>در صورت نیاز، فیلتر پوشه را اضافه کنید</li>
                <li>برای مرتب‌سازی روی سرستون‌ها کلیک کنید</li>
                <li>برای باز کردن، روی هر فایل دابل کلیک کنید</li>
            </ol>

            <hr>

            <h2 style="color: #3498db;">بخش ۲: سازماندهی فایل</h2>
            <p>انتقال دسته‌ای فایل‌ها بر اساس پسوند. انواع فایل را انتخاب کنید، مبدأ و مقصد را مشخص کنید و فایل‌های خود را به‌طور خودکار سازماندهی کنید.</p>

            <h3>نحوه استفاده</h3>
            <ol style="text-align: right;">
                <li>پسوندهای فایل مورد نظر برای سازماندهی را انتخاب کنید</li>
                <li>پوشه مبدأ را انتخاب کنید</li>
                <li>پوشه مقصد را انتخاب کنید</li>
                <li>روی "انتقال فایل‌ها" کلیک کنید</li>
            </ol>

            <hr>

            <h2 style="color: #3498db;">بخش ۳: یابنده هوشمند فایل‌های تکراری</h2>
            
            <h3>⚡ حالت اسکن سریع (توصیه می‌شود)</h3>
            <p><b>نحوه کار:</b> از نرمال‌سازی هوشمند نام فایل برای تشخیص الگوهای رایج کپی استفاده می‌کند.</p>
            
            <h4>چه چیزی را تشخیص می‌دهد:</h4>
            <ul style="text-align: right;">
                <li>فایل‌هایی مانند "document.pdf" و "document (1).pdf"</li>
                <li>فایل‌هایی مانند "photo.jpg" و "photo - Copy.jpg"</li>
                <li>فایل‌هایی مانند "video.mp4" و "video_2.mp4"</li>
            </ul>

            <h4>گزینه‌ها:</h4>
            <ul style="text-align: right;">
                <li><b>تطبیق اندازه فایل:</b> فقط فایل‌های با اندازه یکسان را گروه‌بندی کن (دقیق‌تر)</li>
            </ul>

            <h4>عملکرد:</h4>
            <ul style="text-align: right;">
                <li>سرعت: بسیار سریع (۱۰۰-۱۰۰۰ فایل در ثانیه)</li>
                <li>دقت: خوب برای سناریوهای معمول تکراری</li>
                <li>بهترین برای: پاکسازی سریع فایل‌های دانلود شده/کپی شده</li>
            </ul>

            <h3>🔬 حالت اسکن عمیق (بر اساس محتوا)</h3>
            <p><b>نحوه کار:</b> محتوای باینری واقعی هر فایل را با استفاده از هش رمزنگاری تحلیل می‌کند.</p>

            <h4>چه چیزی را تشخیص می‌دهد:</h4>
            <ul style="text-align: right;">
                <li>فایل‌های با محتوای یکسان حتی اگر نام‌هایشان کاملاً متفاوت باشد</li>
                <li>فایل‌هایی که تغییر نام یافته یا جابجا شده‌اند</li>
                <li>نسخه‌های پشتیبان دقیقاً تکراری</li>
            </ul>

            <h4>الگوریتم‌های هش:</h4>
            <ul style="text-align: right;">
                <li><b>MD5:</b> سریع‌ترین، توصیه می‌شود برای اکثر کاربران</li>
                <li><b>SHA-1:</b> تعادل بین سرعت و امنیت</li>
                <li><b>SHA-256:</b> امن‌ترین، کندترین</li>
            </ul>

            <p><i>همه الگوریتم‌ها دقت ۱۰۰٪ برای تشخیص تکراری دارند.</i></p>

            <h3>⚠️ هشدارهای مهم</h3>
            <ul style="color: #e74c3c; text-align: right;">
                <li><b>حذف دائمی است</b> - فایل‌ها به سطل بازیافت منتقل نمی‌شوند</li>
                <li><b>با دقت بررسی کنید</b> - برخی موارد تکراری ممکن است عمدی باشند</li>
                <li><b>مسیرهای فایل را بررسی کنید</b> - اطمینان حاصل کنید از مکان درست حذف می‌کنید</li>
                <li><b>در صورت تردید، جدیدترین یا بزرگترین را نگه دارید</b></li>
            </ul>

            <hr>

            <h2 style="color: #3498db;">بخش ۴: مبدل فرمت رسانه</h2>
            
            <h3>⚠️ اطلاعات مهم</h3>
            <p style="color: #d32f2f; font-weight: bold;">
                این تبدیل واقعی رسانه است - فایل‌ها رمزگشایی و دوباره رمزگذاری می‌شوند، فقط تغییر نام نمی‌یابند!
            </p>
            
            <h3>نیازمندی‌ها</h3>
            <ul style="text-align: right;">
                <li><b>Pillow:</b> برای تبدیل تصویر نیاز است (نصب: <code>pip install Pillow</code>)</li>
                <li><b>ffmpeg:</b> برای تبدیل ویدیو/صدا نیاز است (دانلود از ffmpeg.org)</li>
            </ul>
            
            <h3>فرمت‌های پشتیبانی شده</h3>
            <h4>تصاویر</h4>
            <ul style="text-align: right;">
                <li><b>ورودی:</b> JPG، PNG، BMP، WebP، TIFF</li>
                <li><b>خروجی:</b> JPG، PNG، WebP</li>
            </ul>
            
            <h4>ویدیوها</h4>
            <ul style="text-align: right;">
                <li><b>ورودی:</b> MP4، MKV، AVI، MOV، WebM</li>
                <li><b>خروجی:</b> MP4، MKV، WebM</li>
            </ul>
            
            <h4>صداها</h4>
            <ul style="text-align: right;">
                <li><b>ورودی:</b> MP3، WAV، AAC، FLAC، OGG</li>
                <li><b>خروجی:</b> MP3، WAV، AAC، FLAC</li>
            </ul>

            <hr>

            <h2 style="color: #3498db;">بخش ۵: جستجوی چهره (۱۰۰٪ آفلاین)</h2>

            <h3>تشخیص قدرتمند چهره به‌صورت آفلاین</h3>
            <p>تمام عکس‌های حاوی یک فرد خاص را با استفاده از تشخیص چهره یادگیری عمیق پیدا کنید - 
            کاملاً آفلاین، بدون نیاز به اینترنت!</p>

            <h3>نیازمندی‌ها</h3>
            <ul style="text-align: right;">
                <li><b>کتابخانه face_recognition:</b> نصب با <code>pip install face_recognition</code></li>
                <li><b>راه‌اندازی اولیه:</b> مدل‌ها یک بار دانلود و به‌صورت محلی ذخیره می‌شوند</li>
                <li><b>پس از نصب:</b> کاملاً آفلاین کار می‌کند</li>
            </ul>

            <h3>نحوه کار</h3>
            <ol style="text-align: right;">
                <li><b>انتخاب عکس مرجع:</b> یک عکس واضح از فرد را انتخاب کنید</li>
                <li><b>انتخاب چهره‌ها:</b> چهره‌(های) مورد نظر برای تطبیق را از چهره‌های شناسایی شده انتخاب کنید</li>
                <li><b>انتخاب پوشه جستجو:</b> پوشه حاوی عکس‌ها را برای جستجو انتخاب کنید</li>
                <li><b>تنظیم شباهت:</b> آستانه را تنظیم کنید (۶۰-۷۰٪ توصیه می‌شود)</li>
                <li><b>شروع جستجو:</b> برنامه تمام تصاویر را اسکن کرده و موارد مطابق را پیدا می‌کند</li>
                <li><b>سازماندهی نتایج:</b> موارد مطابق را در پوشه‌های سازماندهی شده کپی کنید</li>
            </ol>

            <h3>جدید: پشتیبانی از چند چهره و خروجی سازماندهی شده</h3>
            <ul style="text-align: right;">
                <li><b>چند چهره:</b> جستجو برای چندین نفر به‌طور همزمان</li>
                <li><b>پوشه‌های جداگانه:</b> هر فرد پوشه مخصوص خود را دارد</li>
                <li><b>پوشه همه با هم:</b> تصاویری که همه افراد انتخاب شده با هم در آن‌ها هستند</li>
            </ul>

            <h3>بهترین شیوه‌ها برای عکس‌های مرجع</h3>
            <ul style="text-align: right;">
                <li>✓ از یک عکس واضح و با نور خوب استفاده کنید</li>
                <li>✓ چهره باید واضح و بدون مانع باشد</li>
                <li>✓ عکس‌های رو به جلو بهتر کار می‌کنند</li>
                <li>✓ از عینک آفتابی، سایه‌های سنگین یا زوایای شدید اجتناب کنید</li>
            </ul>

            <hr>

            <h2 style="color: #3498db;">بخش ۶: تنظیمات</h2>

            <h3>تنظیمات موجود</h3>
            <ul style="text-align: right;">
                <li><b>زبان:</b> تغییر بین انگلیسی و فارسی</li>
                <li><b>تم:</b> انتخاب تم روشن، تیره یا آبی</li>
                <li><b>فونت:</b> سفارشی‌سازی خانواده و اندازه فونت</li>
                <li><b>پردازش پس‌زمینه:</b> فعال/غیرفعال کردن نمایه‌سازی خودکار فایل</li>
            </ul>

            <h3>نحوه تغییر تنظیمات</h3>
            <ol style="text-align: right;">
                <li>به برگه تنظیمات بروید</li>
                <li>تنظیمات دلخواه خود را تنظیم کنید</li>
                <li>روی "اعمال تنظیمات" کلیک کنید</li>
                <li>برخی تغییرات ممکن است نیاز به راه‌اندازی مجدد برنامه داشته باشند</li>
            </ol>

            <hr>

            <h2 style="color: #3498db;">میانبرهای صفحه کلید</h2>
            <ul style="text-align: right;">
                <li><b>Ctrl+F:</b> فوکوس جستجو (برگه مرورگر)</li>
                <li><b>F5:</b> تازه‌سازی</li>
                <li><b>Escape:</b> پاک کردن/لغو</li>
            </ul>

            <h2 style="color: #3498db;">نکات عملکرد</h2>
            <ul style="text-align: right;">
                <li>نمایه‌سازی اولیه ممکن است ۵-۱۵ دقیقه برای درایوهای بزرگ طول بکشد</li>
                <li>جستجو پس از تکمیل نمایه‌سازی فوری است</li>
                <li>اسکن عمیق می‌تواند از چند دقیقه تا چند ساعت بسته به اندازه داده‌ها طول بکشد</li>
                <li>تشخیص چهره: حدود ۱-۳ ثانیه به ازای هر تصویر</li>
                <li>سایر برنامه‌ها را در طول عملیات سنگین ببندید</li>
            </ul>

            <hr>

            <div style="margin-top: 40px; padding: 20px; background-color: #1f2933; border-right: 4px solid #3498db; border-radius: 5px;">
                <h2 style="color: #969696; margin-top: 0;">درباره</h2>
                <p style="font-size: 12pt; margin: 10px 0;">
                    <b>نام برنامه:</b> مدیریت پیشرفته فایل‌ها و سازماندهی<br>
                    <b>نسخه:</b> 1.0<br>
                    <b>ساخته شده توسط:</b> <span style="color: #3498db; font-weight: bold;">علی دولابی</span><br>
                    <b>پلتفرم:</b> ویندوز، مک‌او‌اس، لینوکس<br>
                    <b>تکنولوژی:</b> <br>
                    ◙ پایتون<br>
                    PyQt6 ◙<br>
                </p>
                <p style="margin-top: 15px; font-style: italic; color: #666;">
                    اگر این برنامه برای شما مفید است، لطفاً با کلیک روی دکمه کمک مالی در بالا، 
                    از توسعه آن حمایت کنید. حمایت شما به نگهداری و بهبود این برنامه کمک می‌کند!
                </p>
            </div>
            <hr>
            
        </body>
        </html>
        """

# ============================================================================
# SETTING TAB
# ============================================================================

class SettingsTab(QWidget):
    """Settings and preferences with database persistence"""
    
    settings_changed = pyqtSignal()
    
    def __init__(self, translation_manager: TranslationManager, theme_manager: ThemeManager, 
                 db_manager: DatabaseManager, parent=None):
        super().__init__(parent)
        self.translation_manager = translation_manager
        self.theme_manager = theme_manager
        self.db_manager = db_manager
        self.color_buttons = {}
        self.init_ui()
        self.load_saved_settings()
    
    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()
        
        settings_group = QGroupBox("Application Settings")
        settings_layout = QGridLayout()
        
        row = 0
        
        settings_layout.addWidget(QLabel("Language:"), row, 0)
        self.language_combo = QComboBox()
        self.language_combo.addItems(['English', 'فارسی (Persian)'])
        self.language_combo.setCurrentIndex(0)
        settings_layout.addWidget(self.language_combo, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Theme:"), row, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(['Light', 'Dark', 'Blue', 'Custom'])
        self.theme_combo.setCurrentIndex(0)
        self.theme_combo.currentIndexChanged.connect(self.on_theme_changed)
        settings_layout.addWidget(self.theme_combo, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Font Family:"), row, 0)
        self.font_combo = QComboBox()
        self.font_combo.addItems(['Default', 'Arial', 'Courier New', 'Times New Roman', 'Verdana'])
        self.font_combo.setCurrentIndex(0)
        settings_layout.addWidget(self.font_combo, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Font Size:"), row, 0)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(10)
        self.font_size_spin.setSuffix(" pt")
        settings_layout.addWidget(self.font_size_spin, row, 1)
        
        row += 1
        self.background_processing_check = QCheckBox("Enable background processing (file indexing, scanning)")
        self.background_processing_check.setChecked(True)
        settings_layout.addWidget(self.background_processing_check, row, 0, 1, 2)
        
        settings_layout.setColumnStretch(2, 1)
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        self.custom_theme_group = QGroupBox("Custom Theme Colors")
        custom_theme_layout = QGridLayout()
        
        color_elements = [
            ('background', 'Background Color'),
            ('foreground', 'Text Color'),
            ('button_bg', 'Button Background'),
            ('button_fg', 'Button Text'),
            ('button_hover', 'Button Hover'),
            ('input_border', 'Input Border'),
            ('group_border', 'Group Border'),
            ('selection', 'Selection/Accent')
        ]
        
        for idx, (key, label) in enumerate(color_elements):
            custom_theme_layout.addWidget(QLabel(label + ":"), idx, 0)
            
            color_btn = QPushButton("Choose Color")
            color_btn.setProperty('color_key', key)
            color_btn.clicked.connect(lambda checked, k=key: self.choose_color(k))
            
            color_preview = QLabel("      ")
            color_preview.setStyleSheet("border: 1px solid #000; background-color: #ffffff;")
            color_preview.setFixedSize(50, 25)
            
            color_layout = QHBoxLayout()
            color_layout.addWidget(color_btn)
            color_layout.addWidget(color_preview)
            color_layout.addStretch()
            
            custom_theme_layout.addLayout(color_layout, idx, 1)
            
            self.color_buttons[key] = {
                'button': color_btn,
                'preview': color_preview,
                'color': '#ffffff'
            }
        
        self.custom_theme_group.setLayout(custom_theme_layout)
        self.custom_theme_group.setVisible(False)
        layout.addWidget(self.custom_theme_group)
        
        buttons_layout = QHBoxLayout()
        
        self.apply_btn = QPushButton("Apply Settings")
        self.apply_btn.clicked.connect(self.apply_settings)
        self.apply_btn.setStyleSheet("background-color: #27ae60; font-weight: bold;")
        
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.clicked.connect(self.reset_defaults)
        
        self.reset_custom_btn = QPushButton("Reset Custom Theme")
        self.reset_custom_btn.clicked.connect(self.reset_custom_theme)
        
        buttons_layout.addWidget(self.apply_btn)
        buttons_layout.addWidget(self.reset_btn)
        buttons_layout.addWidget(self.reset_custom_btn)
        buttons_layout.addStretch()
        
        layout.addLayout(buttons_layout)
        
        info_label = QLabel(
            "Note: Some settings require application restart to fully take effect."
        )
        info_label.setStyleSheet("color: #666; font-style: italic; padding: 10px;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def on_theme_changed(self, index: int):
        """Handle theme selection change"""
        theme_name = self.theme_combo.currentText()
        self.custom_theme_group.setVisible(theme_name == 'Custom')
    
    def choose_color(self, color_key: str):
        """Open color picker dialog"""
        current_color = QColor(self.color_buttons[color_key]['color'])
        
        color = QColorDialog.getColor(current_color, self, f"Choose {color_key} Color")
        
        if color.isValid():
            color_hex = color.name()
            self.color_buttons[color_key]['color'] = color_hex
            self.color_buttons[color_key]['preview'].setStyleSheet(
                f"border: 1px solid #000; background-color: {color_hex};"
            )
    
    def load_saved_settings(self):
        """Load settings from database"""
        lang = self.db_manager.get_preference('language', 'en')
        lang_index = 0 if lang == 'en' else 1
        self.language_combo.setCurrentIndex(lang_index)
        
        theme = self.db_manager.get_preference('theme', 'light')
        theme_map = {'light': 0, 'dark': 1, 'blue': 2, 'custom': 3}
        self.theme_combo.setCurrentIndex(theme_map.get(theme, 0))
        
        font_family = self.db_manager.get_preference('font_family', 'Default')
        font_index = self.font_combo.findText(font_family)
        if font_index >= 0:
            self.font_combo.setCurrentIndex(font_index)
        
        font_size = self.db_manager.get_preference('font_size', 10)
        self.font_size_spin.setValue(font_size)
        
        bg_processing = self.db_manager.get_preference('background_processing', True)
        self.background_processing_check.setChecked(bg_processing)
        
        custom_colors = self.db_manager.get_preference('custom_theme_colors', {})
        if custom_colors:
            self.theme_manager.load_custom_colors(custom_colors)
            for key, color in custom_colors.items():
                if key in self.color_buttons:
                    self.color_buttons[key]['color'] = color
                    self.color_buttons[key]['preview'].setStyleSheet(
                        f"border: 1px solid #000; background-color: {color};"
                    )
        else:
            default_colors = self.theme_manager.get_custom_colors()
            for key, color in default_colors.items():
                if key in self.color_buttons:
                    self.color_buttons[key]['color'] = color
                    self.color_buttons[key]['preview'].setStyleSheet(
                        f"border: 1px solid #000; background-color: {color};"
                    )
        
        self.translation_manager.set_language(lang)
        self.theme_manager.set_theme(theme)
        
        self.custom_theme_group.setVisible(theme == 'custom')
    
    def apply_settings(self):
        """Apply and save settings"""
        lang_map = {'English': 'en', 'فارسی (Persian)': 'fa'}
        selected_lang = self.language_combo.currentText()
        lang_code = lang_map.get(selected_lang, 'en')
        self.translation_manager.set_language(lang_code)
        self.db_manager.save_preference('language', lang_code)
        
        theme_map = {'Light': 'light', 'Dark': 'dark', 'Blue': 'blue', 'Custom': 'custom'}
        selected_theme = self.theme_combo.currentText()
        theme_code = theme_map.get(selected_theme, 'light')
        self.theme_manager.set_theme(theme_code)
        self.db_manager.save_preference('theme', theme_code)
        
        if theme_code == 'custom':
            custom_colors = {}
            for key, widgets in self.color_buttons.items():
                color = widgets['color']
                custom_colors[key] = color
                self.theme_manager.set_custom_color(key, color)
            
            self.db_manager.save_preference('custom_theme_colors', custom_colors)
        
        font_family = self.font_combo.currentText()
        self.db_manager.save_preference('font_family', font_family)
        
        font_size = self.font_size_spin.value()
        self.db_manager.save_preference('font_size', font_size)
        
        bg_processing = self.background_processing_check.isChecked()
        self.db_manager.save_preference('background_processing', bg_processing)
        
        self.settings_changed.emit()
        
        QMessageBox.information(
            self,
            "Settings Applied",
            "Settings have been applied and saved successfully.\nSome changes may require restart."
        )
    
    def reset_defaults(self):
        """Reset to default settings"""
        self.language_combo.setCurrentIndex(0)
        self.theme_combo.setCurrentIndex(0)
        self.font_combo.setCurrentIndex(0)
        self.font_size_spin.setValue(10)
        self.background_processing_check.setChecked(True)
        
        self.translation_manager.set_language('en')
        self.theme_manager.set_theme('light')
        
        self.db_manager.save_preference('language', 'en')
        self.db_manager.save_preference('theme', 'light')
        self.db_manager.save_preference('font_family', 'Default')
        self.db_manager.save_preference('font_size', 10)
        self.db_manager.save_preference('background_processing', True)
        
        self.settings_changed.emit()
    
    def reset_custom_theme(self):
        """Reset custom theme to default colors"""
        default_colors = {
            'background': '#ffffff',
            'foreground': '#000000',
            'button_bg': '#3498db',
            'button_fg': '#ffffff',
            'button_hover': '#2980b9',
            'input_border': '#bdc3c7',
            'group_border': '#cccccc',
            'selection': '#3498db',
        }
        
        for key, color in default_colors.items():
            if key in self.color_buttons:
                self.color_buttons[key]['color'] = color
                self.color_buttons[key]['preview'].setStyleSheet(
                    f"border: 1px solid #000; background-color: {color};"
                )
                self.theme_manager.set_custom_color(key, color)
        
        self.db_manager.save_preference('custom_theme_colors', default_colors)
        
        if self.theme_combo.currentText() == 'Custom':
            self.settings_changed.emit()
        
        QMessageBox.information(
            self,
            "Custom Theme Reset",
            "Custom theme has been reset to default colors."
        )
    
    def get_background_processing_enabled(self) -> bool:
        """Get background processing setting"""
        return self.background_processing_check.isChecked()
    
    def get_font_settings(self) -> Tuple[str, int]:
        """Get font settings"""
        font_family = self.font_combo.currentText()
        if font_family == 'Default':
            font_family = QApplication.font().family()
        font_size = self.font_size_spin.value()
        return font_family, font_size

# ============================================================================
# MAIN WINDOW
# ============================================================================

class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self):
        super().__init__()
        
        self.db_manager = DatabaseManager()
        self.translation_manager = TranslationManager()
        self.theme_manager = ThemeManager()
        
        self.setWindowTitle(self.translation_manager.get('app_title') + " v1.0")
        self.setGeometry(100, 100, 1200, 800)

        self.init_ui()
        self.create_status_bar()
        self.apply_theme()
    
    def closeEvent(self, event):
        """Handle application close"""
        self.db_manager.close()
        event.accept()

    def init_ui(self):
        """Initialize UI"""
        self.tabs = QTabWidget()

        self.settings_tab = SettingsTab(self.translation_manager, self.theme_manager, self.db_manager)
        self.settings_tab.settings_changed.connect(self.on_settings_changed)
        
        self.explorer_tab = FileExplorerTab(self.db_manager, self.settings_tab)
        self.organizer_tab = FileOrganizerTab(self.db_manager)
        self.duplicate_tab = DuplicateFinderTab()
        self.converter_tab = MediaConverterTab()
        self.face_search_tab = FaceSearchTab()
        self.help_tab = HelpTab(self.translation_manager)
        self.tabs.addTab(self.explorer_tab, self.translation_manager.get('file_explorer'))
        self.tabs.addTab(self.organizer_tab, self.translation_manager.get('file_organizer'))
        self.tabs.addTab(self.duplicate_tab, self.translation_manager.get('duplicate_finder'))
        self.tabs.addTab(self.converter_tab, self.translation_manager.get('media_converter'))
        self.tabs.addTab(self.face_search_tab, self.translation_manager.get('face_search'))
        self.tabs.addTab(self.settings_tab, self.translation_manager.get('settings'))
        self.tabs.addTab(self.help_tab, self.translation_manager.get('help'))

        self.setCentralWidget(self.tabs)

    def create_status_bar(self):
        """Create status bar"""
        self.statusBar().showMessage(self.translation_manager.get('ready') + " - System indexing in background")

    def apply_theme(self):
        """Apply current theme"""
        self.setStyleSheet(self.theme_manager.get_stylesheet())
        
        font_family, font_size = self.settings_tab.get_font_settings()
        font = QFont(font_family, font_size)
        QApplication.instance().setFont(font)

    def on_settings_changed(self):
        """Handle settings changes"""
        self.apply_theme()
        
        self.setWindowTitle(self.translation_manager.get('app_title') + " v1.0")
        
        self.tabs.setTabText(0, self.translation_manager.get('file_explorer'))
        self.tabs.setTabText(1, self.translation_manager.get('file_organizer'))
        self.tabs.setTabText(2, self.translation_manager.get('duplicate_finder'))
        self.tabs.setTabText(3, self.translation_manager.get('media_converter'))
        self.tabs.setTabText(4, self.translation_manager.get('face_search'))
        self.tabs.setTabText(5, self.translation_manager.get('settings'))
        self.tabs.setTabText(6, self.translation_manager.get('help'))
        
        self.statusBar().showMessage(self.translation_manager.get('ready'))


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main entry point"""
    ffmpeg_path = get_ffmpeg_path()
    os.system(f'"{ffmpeg_path}" -version')

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    app.setApplicationName("FileScope")
    app.setApplicationVersion("1.0")

    window = MainWindow()
    window.showMaximized()  # Start maximized instead of normal show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
