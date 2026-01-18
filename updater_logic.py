import os
import winreg
import re
import json
import urllib.request
import zipfile
import shutil
import tempfile
import hashlib
import sys

# Import patcher module if available
try:
    patcher_path = os.path.join(os.path.dirname(__file__), 'patcher')
    if patcher_path not in sys.path:
        sys.path.insert(0, patcher_path)
    from patcher import patcher
    PATCHER_AVAILABLE = True
except ImportError:
    PATCHER_AVAILABLE = False
    patcher = None

class GameInfo:
    def __init__(self, metadata_path="metadata.json", metadata_url=None):
        self.metadata_path = metadata_path
        self.metadata_url = metadata_url  # GitHub Gist URL or other remote URL
        self.install_path = self.find_game_path()
        self.version = self.get_game_version()
        self.manifest = self.load_manifest() # Load manifest first
        self.installed_dlcs = self.get_installed_dlcs()
    
    def diagnose_version_detection(self):
        """Diagnostic complet pour vérifier la détection de version."""
        diagnostic = {
            "install_path": self.install_path,
            "version_detected": self.version,
            "default_ini_paths": [],
            "files_checked": []
        }
        
        if not self.install_path:
            diagnostic["error"] = "Chemin d'installation non trouvé"
            return diagnostic
        
        # Vérifier tous les chemins possibles
        possible_paths = [
            os.path.join(self.install_path, "Game", "Bin", "Default.ini"),
            os.path.join(self.install_path, "Game-cracked", "Bin", "Default.ini"),
        ]
        
        for path in possible_paths:
            file_info = {
                "path": path,
                "exists": os.path.exists(path),
                "readable": False,
                "content_preview": None
            }
            
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read(1000)  # Premiers 1000 caractères (≈30 lignes)
                        file_info["readable"] = True
                        file_info["content_preview"] = content
                        # Chercher la ligne gameversion
                        for line in content.split('\n'):
                            if 'gameversion' in line.lower():
                                file_info["version_line"] = line.strip()
                                break
                except Exception as e:
                    file_info["error"] = str(e)
            
            diagnostic["files_checked"].append(file_info)
        
        return diagnostic

    def clean_url(self, url):
        """Convert known landing pages to direct download links."""
        if not url: return url
        # Pixeldrain: https://pixeldrain.com/u/ID -> https://pixeldrain.com/api/file/ID
        if "pixeldrain.com/u/" in url:
            return url.replace("/u/", "/api/file/")
        # Hugging Face: blob/main -> resolve/main (direct download)
        if "huggingface.co/" in url and "/blob/" in url:
            direct = url.replace("/blob/", "/resolve/")
            # Ensure download query param for reliability
            if "?download=true" not in direct:
                direct += "?download=true"
            return direct
        return url

    def find_game_path(self):
        """Try to find the game installation path from the registry."""
        registry_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Maxis\The Sims 4"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Maxis\The Sims 4"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Maxis\The Sims 4"),
        ]
        
        for root, path in registry_paths:
            try:
                with winreg.OpenKey(root, path) as key:
                    install_dir, _ = winreg.QueryValueEx(key, "Install Dir")
                    if os.path.exists(install_dir):
                        return install_dir
            except WindowsError:
                continue
                
        # Default fallback if registry fails (common paths)
        common_paths = [
            r"C:\Program Files\EA Games\The Sims 4",
            r"C:\Program Files (x86)\Origin Games\The Sims 4",
            r"D:\Games\The Sims 4",
            r"E:\Games\The Sims 4",
        ]
        for path in common_paths:
            if os.path.exists(path):
                return path
        
        return None

    def get_game_version(self):
        """Read the game version from Default.ini."""
        if not self.install_path:
            return "Unknown"
        
        # Chemins possibles pour Default.ini
        possible_paths = [
            os.path.join(self.install_path, "Game", "Bin", "Default.ini"),
            os.path.join(self.install_path, "Game-cracked", "Bin", "Default.ini"),
        ]
        
        for default_ini in possible_paths:
            if os.path.exists(default_ini):
                try:
                    # Essayer plusieurs encodages
                    for encoding in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
                        try:
                            with open(default_ini, 'r', encoding=encoding) as f:
                                content = f.read()
                                # Chercher gameversion avec différentes variantes
                                patterns = [
                                    r"gameversion\s*=\s*([\d\.]+)",  # gameversion = 1.120.117.1030
                                    r"GameVersion\s*=\s*([\d\.]+)",  # GameVersion = ...
                                    r"GAMEVERSION\s*=\s*([\d\.]+)",  # GAMEVERSION = ...
                                ]
                                for pattern in patterns:
                                    match = re.search(pattern, content, re.IGNORECASE)
                                    if match:
                                        version = match.group(1).strip()
                                        # Valider que c'est bien un numéro de version
                                        if re.match(r'^\d+(\.\d+)+$', version):
                                            return version
                            break  # Si on a réussi à lire, pas besoin des autres encodages
                        except (UnicodeDecodeError, UnicodeError):
                            continue  # Essayer l'encodage suivant
                except Exception as e:
                    # Log l'erreur mais continue avec le fichier suivant
                    print(f"Warning: Could not read {default_ini}: {e}")
                    continue
        
        return "Unknown"

    def get_installed_dlcs(self):
        """List folders that look like DLCs or custom bundles."""
        if not self.install_path:
            return []
            
        dlcs = []
        # Standard Sims 4 pattern
        pattern = re.compile(r"^(EP|GP|SP|FP)\d+$", re.IGNORECASE)
        
        # Also get all codes from manifest to allow custom names
        manifest_codes = [d['code'].upper() for d in self.manifest.get("dlcs", [])]
        
        try:
            for item in os.listdir(self.install_path):
                upper_item = item.upper()
                if os.path.isdir(os.path.join(self.install_path, item)):
                    if pattern.match(upper_item) or upper_item in manifest_codes:
                        dlcs.append(upper_item)
        except Exception:
            pass
        return sorted(list(set(dlcs)))

    def load_manifest(self):
        """Load metadata.json from remote URL (GitHub Gist) or local file.
        Priority: Remote URL > Local file (fallback)
        """
        data = None
        
        # Try remote URL first (GitHub Gist)
        if self.metadata_url:
            try:
                with urllib.request.urlopen(self.metadata_url, timeout=5) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    print(f"[INFO] Metadata chargé depuis: {self.metadata_url}")
            except Exception as e:
                print(f"[WARNING] Impossible de charger metadata depuis {self.metadata_url}: {e}")
                print("[INFO] Utilisation du fichier local en fallback...")
        
        # Fallback to local file
        if data is None and os.path.exists(self.metadata_path):
            try:
                with open(self.metadata_path, 'r') as f:
                    data = json.load(f)
                    print(f"[INFO] Metadata chargé depuis fichier local: {self.metadata_path}")
            except Exception as e:
                print(f"[ERROR] Impossible de charger {self.metadata_path}: {e}")
                return {"dlcs": [], "patches": [], "latest_version": "Unknown"}
        
        if data is None:
            print(f"[ERROR] Aucun metadata trouvé (URL: {self.metadata_url}, fichier: {self.metadata_path})")
            return {"dlcs": [], "patches": [], "latest_version": "Unknown"}
        
        # Normalize URLs with base_url
        base_url = data.get("base_url", "").rstrip('/')
        if base_url:
            for dlc in data.get("dlcs", []):
                url = dlc.get("url")
                if isinstance(url, str) and url.startswith('/'):
                    dlc["url"] = f"{base_url}{url}"
                elif isinstance(url, list):
                    dlc["url"] = [f"{base_url}{u}" if u.startswith('/') else u for u in url]
            
            for patch in data.get("patches", []):
                url = patch.get("url")
                if isinstance(url, str) and url.startswith('/'):
                    patch["url"] = f"{base_url}{url}"
                elif isinstance(url, list):
                    patch["url"] = [f"{base_url}{u}" if u.startswith('/') else u for u in url]
            
            if "crack" in data and data["crack"].get("url", "").startswith('/'):
                data["crack"]["url"] = f"{base_url}{data['crack']['url']}"
        
        # Clean URLs
        for dlc in data.get("dlcs", []):
            url = dlc.get("url")
            if isinstance(url, list):
                dlc["url"] = [self.clean_url(u) for u in url]
            else:
                dlc["url"] = self.clean_url(url)
        
        for patch in data.get("patches", []):
            url = patch.get("url")
            if isinstance(url, list):
                patch["url"] = [self.clean_url(u) for u in url]
            else:
                patch["url"] = self.clean_url(url)
        
        if "crack" in data:
            data["crack"]["url"] = self.clean_url(data["crack"].get("url", ""))
                    
        return data

    def get_patch_path(self):
        """Find a sequence of patches to get to the latest version."""
        current = self.version
        target = self.manifest.get("latest_version")
        if current == target or target == "Unknown":
            return []
            
        patches = self.manifest.get("patches", [])
        if not patches:
            return []
        
        # For Sims 4, usually there's a direct patch from current to latest
        # Return the first patch that matches the target version
        for p in patches:
            if p.get("version") == target:
                return [p]
        
        # If exact match not found, return all patches (will apply sequentially)
        return patches if patches else []

    def get_available_dlcs(self, include_installed=True):
        """Return DLCs from manifest. If include_installed is True, returns all DLCs with status."""
        results = []
        installed = [d.upper() for d in self.get_installed_dlcs()]
        for dlc in self.manifest.get("dlcs", []):
            # Support for multiple codes (bundle)
            codes = [c.strip().upper() for c in dlc["code"].split(',')]
            
            # If any part of the bundle is missing, 
            # we consider the whole entry as NOT installed for the UI pre-selection.
            is_bundle_complete = all(code in installed for code in codes)
            
            dlc_with_status = dlc.copy()
            dlc_with_status["is_installed"] = is_bundle_complete
            
            if include_installed or not is_bundle_complete:
                results.append(dlc_with_status)
        
        # Sort logic: 
        # 1. Mega bundles (codes with commas or "bundle") first.
        # 2. Standard types: EP > GP > SP > FP.
        # 3. Numerical order within each type.
        def sort_key(d):
            code = d["code"].split(',')[0].strip().upper()
            if "BUNDLE" in code or ',' in d["code"]:
                return (0, 0)
            
            match = re.match(r"^([A-Z]+)(\d+)$", code)
            if match:
                prefix, num = match.groups()
                priority = {"EP": 1, "GP": 2, "SP": 3, "FP": 4}.get(prefix, 5)
                return (priority, int(num))
            return (6, code)

        results.sort(key=sort_key)
        return results

    def check_languages(self, dlc_code):
        """Check if language files are missing for an installed DLC."""
        if not self.install_path: return "missing"
        
        # Paths where language files usually reside
        dlc_path = os.path.join(self.install_path, dlc_code)
        if not os.path.exists(dlc_path): return "missing"
        
        # For simplicity, we check if at least one Strings_*.package exists
        # usually in Data/Client or similar.
        found = False
        for root, dirs, files in os.walk(dlc_path):
            for file in files:
                if file.startswith("Strings_") and file.endswith(".package"):
                    # Specifically look for French if possible, but any Strings_ is a good sign
                    if "FRA_FR" in file.upper():
                        return "ok"
                    found = True
        return "ok" if found else "missing_lang"

    def get_ea_token(self):
        """Try to retrieve the EA access token using the provided get_token logic."""
        script_dir = os.path.dirname(self.metadata_path)
        token_script = os.path.join(script_dir, "ea-get-token", "get_token.py")
        
        if not os.path.exists(token_script):
            return None
            
        import subprocess
        try:
            # We run it and capture the output (it prints 4 lines: token, user, pd, psd)
            # We only need the first line (token)
            process = subprocess.Popen(['python', token_script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # Send an enter to close it if it waits (though our captured stdout might block)
            # Actually, get_token.py has input("Press enter to exit.") at the end
            stdout, stderr = process.communicate(input="\n")
            
            lines = [line.strip() for line in stdout.split('\n') if line.strip()]
            # Find the line that looks like "Got token!" and take the NEXT line
            for i, line in enumerate(lines):
                if "Got token!" in line and i + 1 < len(lines):
                    return lines[i+1]
        except Exception:
            pass
        return None

    def is_unlocker_installed(self):
        """Check if EA DLC Unlocker v2 is installed."""
        # Check AppData for anadius config
        appdata = os.getenv('APPDATA')
        if appdata:
            anadius_path = os.path.join(appdata, "anadius", "EA DLC Unlocker v2")
            if os.path.exists(anadius_path):
                return True
        
        # Check for proxy DLL in game path as fallback
        if self.install_path:
            bin_path = os.path.join(self.install_path, "Game", "Bin")
            if os.path.exists(os.path.join(bin_path, "version.dll")):
                return True
                
        return False
        """Check if a ZIP for this DLC already exists in the 'zips' folder."""
        zips_dir = os.path.join(os.path.dirname(self.metadata_path), "zips")
        if not os.path.exists(zips_dir):
            return None
            
        # Search for any zip file containing the code
        pattern = re.compile(rf".*{dlc_code}.*zip$", re.IGNORECASE)
        try:
            for item in os.listdir(zips_dir):
                if pattern.match(item):
                    return os.path.join(zips_dir, item)
        except Exception:
            pass
        return None

    def calculate_md5(self, file_path, progress_callback=None):
        """Calculate MD5 of a file with optional progress updates."""
        hash_md5 = hashlib.md5()
        try:
            total_size = os.path.getsize(file_path)
            processed = 0
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
                    processed += len(chunk)
                    if progress_callback:
                        progress_callback(processed, total_size)
            return hash_md5.hexdigest()
        except Exception:
            return None

    def verify_files(self, progress_callback=None):
        """Verify local files based on hashes in manifest or ts4installer-tumblr-files."""
        if not self.install_path:
            return [] # No install path
        
        # Try to load hashes from ts4installer-tumblr-files first
        hashes = None
        try:
            from hash_loader import load_hashes_for_version
            hashes = load_hashes_for_version(self.version)
            if hashes:
                print(f"[INFO] Using hashes from ts4installer-tumblr-files for version {self.version}")
        except ImportError:
            pass
        
        # Fallback to manifest hashes if available
        if not hashes and "hashes" in self.manifest:
            hashes = self.manifest["hashes"]
            print("[INFO] Using hashes from metadata manifest")
        
        if not hashes:
            return [] # No hashes to verify
            
        corrupted = []
        total_files = len(hashes)
        
        for i, (rel_path, expected_md5) in enumerate(hashes.items()):
            full_path = os.path.join(self.install_path, rel_path)
            if progress_callback:
                progress_callback(i, total_files, rel_path)
                
            if not os.path.exists(full_path):
                corrupted.append(rel_path)
                continue
                
            actual_md5 = self.calculate_md5(full_path)
            if actual_md5 and actual_md5.upper() != expected_md5.upper():
                corrupted.append(rel_path)
                
        return corrupted

    def download_file(self, url, destination, progress_callback=None):
        """Download a file with progress updates."""
        try:
            # Check if it's a known non-direct link
            if "/u/" in url or "/d/" in url:
                # Still try, but it's likely to fail or download HTML
                pass

            # Add User-Agent to avoid some blocks
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                # Check for HTML content (likely a landing page)
                content_type = response.info().get('Content-Type', '')
                if 'text/html' in content_type:
                    return "Error: This URL points to a landing page, not a direct file. Please download manually and place in the 'zips' folder."

                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                block_size = 8192
                with open(destination, 'wb') as out_file:
                    while True:
                        buffer = response.read(block_size)
                        if not buffer:
                            break
                        downloaded += len(buffer)
                        out_file.write(buffer)
                        if progress_callback:
                            progress_callback(downloaded, total_size)
            return True
        except urllib.error.HTTPError as e:
            return f"HTTP Error {e.code}: {e.reason}. Automation might be blocked (CAPTCHA/Traffic limit)."
        except Exception as e:
            return f"Download failed: {str(e)}"

    def extract_zip(self, zip_path, extract_to, password=None):
        """Extract a ZIP file to a destination."""
        if not zipfile.is_zipfile(zip_path):
            return "Downloaded file is not a valid ZIP. It might be an HTML landing page."
            
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                if password:
                    zip_ref.extractall(extract_to, pwd=password.encode())
                else:
                    zip_ref.extractall(extract_to)
            return True
        except Exception as e:
            return f"Extraction error: {str(e)}"

    def apply_crack(self):
        """Download and apply the crack if necessary."""
        crack_info = self.manifest.get("crack")
        if not crack_info or not crack_info.get("url"):
            return "No crack info available"
            
        dest_zip = os.path.join(tempfile.gettempdir(), "ts4_crack.zip")
        if self.download_file(crack_info["url"], dest_zip):
            result = self.extract_zip(dest_zip, self.install_path, password=crack_info.get("password"))
            os.remove(dest_zip)
            if result is True:
                return "Crack applied successfully"
            return f"Failed to applies crack: {result}"
        return "Failed to download crack"
    
    def apply_patch(self, patch_info, callback=None, updater_tmp=None):
        """
        Download, extract, and apply a patch to the game.
        Supports both .patch files (xdelta) and .zip patches.
        
        Args:
            patch_info: Dictionary with patch info (version, url, type, etc)
            callback: Optional callback function for progress logging
            updater_tmp: Optional temp directory path (uses temp if None)
        
        Returns:
            (success, message) tuple
        """
        import tempfile
        import shutil
        
        def log(msg, level="info"):
            if callback:
                callback(msg, level)
        
        # Determine patch type
        patch_type = patch_info.get("type", "zip")  # "zip" or "patch"
        
        # If .patch file and patcher is available, use it
        if patch_type == "patch" and PATCHER_AVAILABLE:
            return self._apply_xdelta_patch(patch_info, callback, updater_tmp)
        elif patch_type == "patch" and not PATCHER_AVAILABLE:
            log("Patcher module not available, cannot apply .patch file", "red")
            return (False, "Patcher module not installed")
        
        # Otherwise, use traditional zip-based patching
        return self._apply_zip_patch(patch_info, callback, updater_tmp)
    
    def _apply_xdelta_patch(self, patch_info, callback=None, updater_tmp=None):
        """
        Apply a .patch file using the patcher module.
        
        Args:
            patch_info: Dictionary with patch info (version, url, etc)
            callback: Optional callback function for progress logging
            updater_tmp: Optional temp directory path
        
        Returns:
            (success, message) tuple
        """
        def log(msg, level="info"):
            if callback:
                callback(msg, level)
        
        try:
            if updater_tmp is None:
                updater_tmp = os.path.join(tempfile.gettempdir(), 'ts4_updater_v4')
            
            patch_file_dir = os.path.join(updater_tmp, 'patches')
            if not os.path.exists(patch_file_dir):
                os.makedirs(patch_file_dir)
            
            # Download .patch file
            url = patch_info.get("url")
            if not url:
                return (False, "No patch URL provided")
            
            base_url = self.manifest.get("base_url", "").rstrip('/')
            if not url.startswith('http'):
                url = f"{base_url}/{url}"
            
            patch_filename = patch_info.get("filename", "game.patch")
            patch_file_path = os.path.join(patch_file_dir, patch_filename)
            
            log(f"Downloading patch file: {patch_filename}", "orange")
            result = self.download_file(url, patch_file_path, lambda d, t: None)
            
            if result is not True:
                return (False, f"Download error: {result}")
            
            log("Applying patch using patcher module...", "bold")
            
            # Create a simple callback for patcher progress
            def patcher_callback(callback_type, *args):
                if callback_type == patcher.CallbackType.INFO:
                    log(args[0] if args else "Processing...")
                elif callback_type == patcher.CallbackType.PROGRESS:
                    if len(args) >= 2:
                        log(f"Progress: {args[0]}/{args[1]}")
                elif callback_type == patcher.CallbackType.WARNING:
                    log(args[0] if args else "Warning", "orange")
                elif callback_type == patcher.CallbackType.FAILURE:
                    log(args[0] if args else "Failed", "red")
                elif callback_type == patcher.CallbackType.FINISHED:
                    log(args[0] if args else "Completed", "green")
            
            # Initialize patcher
            def ask_question(question, options):
                # Auto-answer questions for automated patching
                return options[0] if options else None
            
            patcher_instance = patcher.Patcher(ask_question, callback=patcher_callback)
            
            # Apply the patch
            patcher_instance.patch(
                patch_file_path,
                self.install_path,
                temp_dir=updater_tmp
            )
            
            # Update version
            patch_version = patch_info.get('version', 'Unknown')
            self.version = self.get_game_version()
            
            log(f"Patch {patch_version} applied successfully!", "green")
            
            # Cleanup
            try:
                os.remove(patch_file_path)
            except:
                pass
            
            return (True, f"Patch {patch_version} applied successfully")
            
        except Exception as e:
            log(f"Patch error: {str(e)}", "red")
            return (False, f"Patch application error: {str(e)}")
    
    def _apply_zip_patch(self, patch_info, callback=None, updater_tmp=None):
        """
        Download and apply a zip-based patch (traditional method).
        Uses organized temp structure: downloading/, extracted/, final/
        
        Args:
            patch_info: Dictionary with patch info (version, url, etc)
            callback: Optional callback function for progress logging
            updater_tmp: Optional temp directory path
        
        Returns:
            (success, message) tuple
        """
        import tempfile
        import shutil
        import subprocess
        from urllib.parse import urlparse
        
        def log(msg, level="info"):
            if callback:
                callback(msg, level)
        
        try:
            if updater_tmp is None:
                updater_tmp = os.path.join(tempfile.gettempdir(), 'ts4_updater_v4')
            
            # Create organized temp structure
            downloading_dir = os.path.join(updater_tmp, 'downloading')
            extracted_dir = os.path.join(updater_tmp, 'extracted')
            final_dir = os.path.join(updater_tmp, 'final')
            
            for d in [downloading_dir, extracted_dir, final_dir]:
                if not os.path.exists(d):
                    os.makedirs(d)
            
            log(f"Patch version: {patch_info.get('version', 'Unknown')}")
            log(f"Using temp directory: {updater_tmp}", "blue")
            
            # Step 1: Download patch files
            urls = patch_info.get("url", [])
            if isinstance(urls, str):
                urls = [urls]

            base_url = self.manifest.get("base_url", "").rstrip('/')

            # Compute total size (best effort) for progress display
            def head_content_length(u: str):
                try:
                    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}, method='HEAD')
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        length = resp.info().get('Content-Length')
                        return int(length) if length else None
                except Exception:
                    return None

            resolved_urls = []
            total_size = 0
            sizes = []
            for url in urls:
                if not url.startswith('http'):
                    resolved = f"{base_url}/{url}"
                else:
                    resolved = url
                resolved_urls.append(resolved)
                size = head_content_length(resolved)
                sizes.append(size)
                if size:
                    total_size += size

            downloaded_total = 0
            downloaded_files = []
            rar_like = False
            for i, full_url in enumerate(resolved_urls, 1):
                expected_size = sizes[i-1] if i-1 < len(sizes) else None

                # Preserve original filename (important for multi-part .rar/.z01)
                parsed = urlparse(full_url)
                filename = os.path.basename(parsed.path)
                if not filename:
                    filename = f"patch_part{i}"
                dest = os.path.join(downloading_dir, filename)

                log(f"Downloading part {i}/{len(resolved_urls)}...", "orange")

                # Progress callback with global percentage
                last_report = {'bytes': 0}
                def progress_cb(done, file_total):
                    nonlocal downloaded_total
                    current = downloaded_total + done
                    if total_size:
                        percent = (current / total_size) * 100
                        # Throttle logs: every ~5 MB or on completion
                        if current - last_report['bytes'] < 5 * 1024 * 1024 and current < total_size:
                            return
                        last_report['bytes'] = current
                        log(f"Progress: {percent:.1f}% ({current/1e6:.1f} MB / {total_size/1e6:.1f} MB)", "blue")
                    else:
                        # Fallback when total size unknown
                        if current - last_report['bytes'] < 5 * 1024 * 1024 and file_total:
                            return
                        last_report['bytes'] = current
                        if file_total:
                            file_percent = (done / file_total) * 100
                            log(f"Part {i}: {file_percent:.1f}% ({done/1e6:.1f} MB)", "blue")
                        else:
                            log(f"Part {i}: {done/1e6:.1f} MB", "blue")

                result = self.download_file(full_url, dest, progress_cb)
                if result is not True:
                    return (False, f"Download error for part {i}: {result}")

                # Update counters after successful download
                try:
                    actual_size = os.path.getsize(dest)
                except OSError:
                    actual_size = expected_size or 0
                downloaded_total += actual_size

                downloaded_files.append(dest)
                lower_name = filename.lower()
                if lower_name.endswith(('.rar', '.7z', '.z01', '.z02')) or '.part' in lower_name:
                    rar_like = True

            # Step 2: Extract downloaded archives
            if rar_like:
                # Require 7-Zip
                sevenz = shutil.which('7z') or shutil.which('7za')
                if not sevenz:
                    return (False, "7-Zip (7z/7za) non trouvé. Installez 7-Zip ou fournissez des patchs .zip.")

                # Find first part if multi-part, otherwise any rar/7z
                first_part = None
                for f in downloaded_files:
                    name = os.path.basename(f).lower()
                    if name.endswith('.part1.rar') or name.endswith('.001') or name.endswith('.z01'):
                        first_part = f
                        break
                if not first_part:
                    # fallback: pick a .rar or .7z or .z01
                    for f in downloaded_files:
                        if f.lower().endswith(('.rar', '.7z', '.z01')):
                            first_part = f
                            break
                if not first_part:
                    return (False, "Impossible de déterminer l'archive principale à extraire.")

                log("Extracting archive(s) with 7-Zip...", "orange")
                try:
                    cmd = [sevenz, 'x', '-y', f"-o{extracted_dir}", first_part]
                    proc = subprocess.run(cmd, capture_output=True, text=True)
                    if proc.returncode != 0:
                        return (False, f"7-Zip error: {proc.stderr.strip() or proc.stdout.strip()}")
                except Exception as e:
                    return (False, f"7-Zip execution failed: {str(e)}")

                # Clean downloaded parts
                for f in downloaded_files:
                    try:
                        os.remove(f)
                    except:
                        pass
            else:
                # Standard ZIP extraction per part
                for i, dest in enumerate(downloaded_files, 1):
                    log(f"Extracting part {i}/{len(downloaded_files)}...")
                    extract_result = self.extract_zip(dest, extracted_dir)
                    if extract_result is not True:
                        return (False, f"Extract error for part {i}: {extract_result}")
                    try:
                        os.remove(dest)
                    except:
                        pass
            
            # Step 3: Move patch folders to game root
            log("Moving patch files to game directory...", "bold")
            
            required_folders = ['Game', 'Data', 'Delta', 'Support']
            moved_folders = []
            
            for folder_name in required_folders:
                # Search for the folder in extracted_dir (may be nested)
                src_path = None
                for root, dirs, files in os.walk(extracted_dir):
                    if folder_name in dirs:
                        src_path = os.path.join(root, folder_name)
                        break
                
                if src_path and os.path.isdir(src_path):
                    dst_path = os.path.join(self.install_path, folder_name)
                    try:
                        # Delete destination if it exists
                        if os.path.isdir(dst_path):
                            log(f"Removing existing {folder_name}/...", "orange")
                            shutil.rmtree(dst_path, ignore_errors=True)
                        
                        # Move (not copy) the folder
                        log(f"Moving {folder_name}/...")
                        shutil.move(src_path, dst_path)
                        log(f"✓ {folder_name}/ moved", "green")
                        moved_folders.append(folder_name)
                    except Exception as e:
                        return (False, f"Error moving {folder_name}/: {str(e)}")
                else:
                    log(f"✗ {folder_name}/ not found in extracted files", "red")
            
            # Step 4: Update game version in Default.ini
            patch_version = patch_info.get('version', 'Unknown')
            default_ini_path = os.path.join(self.install_path, "Game", "Bin", "Default.ini")
            
            if os.path.exists(default_ini_path):
                try:
                    with open(default_ini_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    content = re.sub(
                        r'gameversion\s*=\s*[\d\.]+',
                        f'gameversion = {patch_version}',
                        content,
                        flags=re.IGNORECASE
                    )
                    
                    with open(default_ini_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    
                    log(f"Version updated to {patch_version}", "green")
                except Exception as e:
                    log(f"Could not update version: {str(e)}", "orange")
            
            # Step 5: Clean up temp directories
            log("Cleaning up temporary files...", "blue")
            try:
                shutil.rmtree(updater_tmp, ignore_errors=True)
                log("Temporary files cleaned", "green")
            except:
                pass
            
            # Refresh version
            self.version = self.get_game_version()
            
            return (True, f"Patch {patch_version} applied successfully")
            
        except Exception as e:
            return (False, f"Patch application error: {str(e)}")

if __name__ == "__main__":
    # Test logic
    info = GameInfo()
    print(f"Install Path: {info.install_path}")
    print(f"Version: {info.version}")
    print(f"DLCs Installed: {', '.join(info.installed_dlcs)}")
    print(f"DLCs Available: {', '.join([d['code'] for d in info.get_available_dlcs()])}")
