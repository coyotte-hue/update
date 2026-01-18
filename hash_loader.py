import json
import os

def load_hashes_for_version(version):
    """Charge les hashes MD5 pour une version spécifique depuis ts4installer-tumblr-files."""
    # Chercher dans le workspace et dans l'installation du jeu
    possible_dirs = [
        os.path.join(os.path.dirname(__file__), 'ts4installer-tumblr-files', 'hashes'),
        os.path.join(os.path.dirname(__file__), 'hashes')  # Aussi chercher dans hashes/ direct
    ]
    
    # Ajouter aussi le chemin du jeu si disponible
    try:
        from updater_logic import GameInfo
        info = GameInfo(metadata_path=os.path.join(os.path.dirname(__file__), 'metadata.json'))
        if info.install_path:
            possible_dirs.append(os.path.join(info.install_path, 'ts4installer-tumblr-files', 'hashes'))
    except:
        pass
    
    for hashes_dir in possible_dirs:
        hash_file = os.path.join(hashes_dir, f"{version}.json")
        if os.path.exists(hash_file):
            try:
                with open(hash_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # Le fichier contient: {"crack": [...], "hashes": {...}, "version": "..."}
                # On veut uniquement les hashes "hashes" (fichiers du jeu)
                game_hashes = data.get("hashes", {})
                
                # Convertir les chemins avec / en \ pour Windows
                normalized = {}
                for path, md5 in game_hashes.items():
                    # Normaliser le chemin (/ → \\ sur Windows)
                    win_path = path.replace('/', os.sep)
                    normalized[win_path] = md5.upper()  # MD5 en majuscules
                    
                return normalized
            except Exception as e:
                print(f"Error loading hashes for {version}: {e}")
                continue
    
    return None

def get_latest_hash_version():
    """Lit le fichier latest_version.txt pour connaître la dernière version de hashes."""
    # Chercher dans le workspace et dans l'installation du jeu
    possible_dirs = [
        os.path.join(os.path.dirname(__file__), 'ts4installer-tumblr-files', 'hashes'),
        os.path.join(os.path.dirname(__file__), 'hashes')  # Aussi chercher dans hashes/ direct
    ]
    
    # Ajouter aussi le chemin du jeu si disponible
    try:
        from updater_logic import GameInfo
        info = GameInfo(metadata_path=os.path.join(os.path.dirname(__file__), 'metadata.json'))
        if info.install_path:
            possible_dirs.append(os.path.join(info.install_path, 'ts4installer-tumblr-files', 'hashes'))
    except:
        pass
    
    for hashes_dir in possible_dirs:
        latest_file = os.path.join(hashes_dir, 'latest_version.txt')
        if os.path.exists(latest_file):
            try:
                with open(latest_file, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except:
                continue
    
    return None

if __name__ == "__main__":
    # Test
    version = get_latest_hash_version()
    print(f"Latest hash version: {version}")
    
    if version:
        hashes = load_hashes_for_version(version)
        if hashes:
            print(f"Loaded {len(hashes)} file hashes for version {version}")
            # Montrer quelques exemples
            for i, (path, md5) in enumerate(list(hashes.items())[:5]):
                print(f"  {path}: {md5}")
