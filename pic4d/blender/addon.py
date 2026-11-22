"""Blender add-on: File > Import > pic4D bundle (.json). Builds the animated scene from a
bundle made by ``pic4d prepare`` into a new scene."""

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..styles import list_styles


class PIC4D_OT_import(bpy.types.Operator, ImportHelper):
    """Import a pic4D bundle (choose its scene.json)"""

    bl_idname = "pic4d.import_bundle"
    bl_label = "Import pic4D bundle"
    filename_ext = ".json"
    filter_glob: StringProperty(default="scene.json", options={"HIDDEN"})
    style: EnumProperty(name="Style", items=[(s, s, "") for s in list_styles()], default="dark")
    seconds: FloatProperty(name="Seconds", default=7.0, min=1.0)
    fps: IntProperty(name="FPS", default=30, min=1)

    def execute(self, context):
        from pathlib import Path

        from .build import build_scene

        info = build_scene(Path(self.filepath).parent, self.style, fps=self.fps, seconds=self.seconds, clear=False)
        self.report({"INFO"}, f"pic4D: {info['frames']} frames, style {info['style']}")
        return {"FINISHED"}


def _menu(self, context):
    self.layout.operator(PIC4D_OT_import.bl_idname, text="pic4D bundle (scene.json)")


def register():
    bpy.utils.register_class(PIC4D_OT_import)
    bpy.types.TOPBAR_MT_file_import.append(_menu)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    bpy.utils.unregister_class(PIC4D_OT_import)
