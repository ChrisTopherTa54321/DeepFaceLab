from pathlib import Path
from scandir import scandir
import os

image_extensions = [".jpg", ".jpeg", ".png", ".tif", ".tiff"]

def get_image_paths(dir_path, recursive=False):
    dir_path = Path (dir_path)

    result = []

    for root, subdirs, files in os.walk(dir_path):
        for file in files:
            if any([file.lower().endswith(ext) for ext in image_extensions]):
                result.append(os.path.join(root,file) )
        if not recursive:
            break
    return result

def get_image_unique_filestem_paths(dir_path, verbose=False, recursive=False):
    result = get_image_paths(dir_path, recursive)
    result_dup = set()

    for f in result[:]:
        f_stem = Path(f).stem
        if f_stem in result_dup:
            result.remove(f)
            if verbose:
                print ("Duplicate filenames are not allowed, skipping: %s" % Path(f).name )
            continue
        result_dup.add(f_stem)

    return result

def get_all_dir_names_startswith (dir_path, startswith):
    dir_path = Path (dir_path)
    startswith = startswith.lower()

    result = []
    if dir_path.exists():
        for x in list(scandir(str(dir_path))):
            if x.name.lower().startswith(startswith):
                result.append ( x.name[len(startswith):] )
    return result
