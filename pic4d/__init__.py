"""pic4D: photonic integrated circuits, animated in Blender.

Pure-Python parts (no Blender needed): :mod:`pic4d.colors` (wavelength -> colour),
:mod:`pic4d.layout` (GDS import, legibility), :mod:`pic4d.paths` (light paths from
polygons), :mod:`pic4d.field` (simulated-field format), :mod:`pic4d.bundle` (scene
file -> bundle), :mod:`pic4d.camera`, :mod:`pic4d.overlay` and :mod:`pic4d.video`.
The Blender side is :mod:`pic4d.blender`.
"""

__version__ = "0.1.0"

bl_info = {
    "name": "pic4D",
    "author": "SU3 Labs",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > pic4D bundle",
    "description": "Photonic integrated circuits with animated light",
    "category": "Import-Export",
}


def register():  # Blender add-on entry points
    from .blender import addon

    addon.register()


def unregister():
    from .blender import addon

    addon.unregister()
