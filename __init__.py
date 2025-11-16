bl_info = { 
    "name": "Origin_move_like_maya",
    "author": "Deepak",
    "version": (1, 2, 0),
    "blender": (4, 5, 3),
    "description": "Press D to toggle pivot move mode; LMB drag to move pivot; press S to toggle snapping.",
    "category": "3D View",
}

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector, Matrix


class ViewportState:
    def __init__(self):
        self.shading_type = {}
        self.show_wireframes = {}
        self.prev_snap = None
        self.prev_snap_elements = None
        self.prev_show_gizmo_object_translate = {}

    def save_and_enable(self, context):
        for area in context.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    ptr = area.as_pointer()
                    self.shading_type[ptr] = space.shading.type
                    self.show_wireframes[ptr] = space.overlay.show_wireframes
                    space.shading.type = "SOLID"
                    space.overlay.show_wireframes = True
                    self.prev_show_gizmo_object_translate[ptr] = space.show_gizmo_object_translate
                    space.show_gizmo_object_translate = True

        ts = context.scene.tool_settings
        self.prev_snap = ts.use_snap
        self.prev_snap_elements = set(ts.snap_elements)
        ts.use_snap = True
        ts.snap_elements = {'VERTEX', 'EDGE', 'FACE'}

    def restore(self, context):
        for area in context.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    ptr = area.as_pointer()
                    if ptr in self.shading_type:
                        space.shading.type = self.shading_type[ptr]
                    if ptr in self.show_wireframes:
                        space.overlay.show_wireframes = self.show_wireframes[ptr]
                    if ptr in self.prev_show_gizmo_object_translate:
                        space.show_gizmo_object_translate = self.prev_show_gizmo_object_translate[ptr]

        ts = context.scene.tool_settings
        if self.prev_snap is not None:
            ts.use_snap = self.prev_snap
        if self.prev_snap_elements is not None:
            ts.snap_elements = set(self.prev_snap_elements)



class VIEW3D_OT_pivot_move_snap_modal(bpy.types.Operator):
    bl_idname = "view3d.pivot_move_snap_modal_toggle"
    bl_label = "Pivot Move Snap (Toggle)"
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}
    
    mouse_x: bpy.props.IntProperty()
    mouse_y: bpy.props.IntProperty()
    is_active = False
    instance = None

    snapping_enabled = True   # <-- NEW: snap state

    def invoke(self, context, event):
        if VIEW3D_OT_pivot_move_snap_modal.is_active:
            self._finish_and_restore(context)
            VIEW3D_OT_pivot_move_snap_modal.is_active = False
            context.workspace.status_text_set(None)
            self.report({'INFO'}, "Pivot Edit Mode: OFF")
            return {'FINISHED'}

        if context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "3D View not active")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            self.report({'WARNING'}, "Switch to Object Mode")
            return {'CANCELLED'}

        self.viewport_state = ViewportState()
        self.dragging = False
        self.mouse_x = event.mouse_region_x
        self.mouse_y = event.mouse_region_y

        self.viewport_state.save_and_enable(context)
        context.window_manager.modal_handler_add(self)

        context.workspace.status_text_set(
            "Pivot Edit: LMB drag to move pivot | D = exit | S = toggle snap"
        )

        VIEW3D_OT_pivot_move_snap_modal.is_active = True
        VIEW3D_OT_pivot_move_snap_modal.instance = self

        self.report({'INFO'}, "Pivot Edit Mode: ON")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        obj = context.active_object
        if obj is None:
            return {'CANCELLED'}

        # EXIT
        if event.type == 'D' and event.value == 'PRESS':
            self._finish_and_restore(context)
            VIEW3D_OT_pivot_move_snap_modal.is_active = False
            context.workspace.status_text_set(None)
            return {'FINISHED'}

        # 🔵 NEW: Snap toggle
        if event.type == 'S' and event.value == 'PRESS':
            self.snapping_enabled = not self.snapping_enabled
            message = "Snapping: ON" if self.snapping_enabled else "Snapping: OFF"
            self.report({'INFO'}, message)
            return {'RUNNING_MODAL'}

        # DRAGGING
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.dragging = True
            self.mouse_x = event.mouse_region_x
            self.mouse_y = event.mouse_region_y
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.dragging = False
            return {'RUNNING_MODAL'}

        if self.dragging and event.type == 'MOUSEMOVE':
            self._update_pivot_from_mouse(context, event, obj)
            return {'RUNNING_MODAL'}

        # ESC or RMB cancels
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._finish_and_restore(context)
            VIEW3D_OT_pivot_move_snap_modal.is_active = False
            context.workspace.status_text_set(None)
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    def _update_pivot_from_mouse(self, context, event, obj):
        region = context.region
        rv3d = context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)

        deps = context.evaluated_depsgraph_get()
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        ray_dir = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

        hit_object, hit_location, _, _, _, _ = context.scene.ray_cast(
            deps, ray_origin, ray_dir
        )

        if not hit_object:
            distance = max((obj.location - ray_origin).length, 1.0)
            hit_location = ray_origin + ray_dir * distance

        # SNAP only if enabled
        if self.snapping_enabled:
            snap_loc = self._snap_to_nearest_element(context, hit_location, 0.15)
            target_world = snap_loc if snap_loc else hit_location
        else:
            target_world = hit_location

        self._move_origin_only(obj, target_world, context)

        obj.update_tag(refresh={'DATA'})
        context.view_layer.update()

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    def _snap_to_nearest_element(self, context, world_point, max_dist=0.15):
        deps = context.evaluated_depsgraph_get()
        best, best_d = None, max_dist

        for ob in context.visible_objects:
            if ob.type != 'MESH':
                continue

            eval_obj = ob.evaluated_get(deps)
            mesh = eval_obj.to_mesh()

            mw = ob.matrix_world

            for v in mesh.vertices:
                wv = mw @ v.co
                d = (wv - world_point).length
                if d < best_d:
                    best, best_d = wv.copy(), d

            for e in mesh.edges:
                v0 = mw @ mesh.vertices[e.vertices[0]].co
                v1 = mw @ mesh.vertices[e.vertices[1]].co
                mid = (v0 + v1) * 0.5
                d = (mid - world_point).length
                if d < best_d:
                    best, best_d = mid.copy(), d

            for p in mesh.polygons:
                wc = mw @ p.center
                d = (wc - world_point).length
                if d < best_d:
                    best, best_d = wc.copy(), d

            eval_obj.to_mesh_clear()

        return best

    def _move_origin_only(self, obj, new_origin_world, context):
        M = obj.matrix_world.copy()
        old_origin_world = M.translation.copy()

        offset_world = new_origin_world - old_origin_world
        M_no_trans = M.copy()
        M_no_trans.translation = (0, 0, 0)
        offset_local = M_no_trans.inverted() @ offset_world

        mesh = obj.data
        mesh.transform(Matrix.Translation(-offset_local))
        mesh.update()

        obj.location = new_origin_world

    def _finish_and_restore(self, context):
        try:
            self.viewport_state.restore(context)
        except:
            pass


addon_keymaps = []

def register():
    bpy.utils.register_class(VIEW3D_OT_pivot_move_snap_modal)

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new(
            VIEW3D_OT_pivot_move_snap_modal.bl_idname,
            'D', 'PRESS'
        )
        addon_keymaps.append((km, kmi))


def unregister():
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    bpy.utils.unregister_class(VIEW3D_OT_pivot_move_snap_modal)


if __name__ == "__main__":
    register()
