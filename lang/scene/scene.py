from email.policy import default
import time, sys, logging, bpy, os
from bpy.types import Scene, Object, Material
from bpy.props import IntVectorProperty
from ...types import BFNamelist, FDSCase, BFException, BFNotImported
from ... import utils
from ..object.MOVE import ON_MOVE

log = logging.getLogger(__name__)

def _import_by_fds_label(scene, context, fds_case, free_text, fds_label=None):
    for fds_namelist in fds_case.get(fds_label, remove=True):
        _import_fds_namelist(scene, context, free_text, fds_namelist)


def _import_fds_namelist(scene, context, free_text, fds_namelist):
    is_imported = False
    fds_label = fds_namelist.fds_label
    bf_namelist = BFNamelist.get_subclass(fds_label=fds_label)
    if bf_namelist:
        hid = f"Imported {fds_label}"
        match bf_namelist.bpy_type:
            case t if t == Object:
                me = bpy.data.meshes.new(hid)  # new Mesh
                ob = bpy.data.objects.new(hid, object_data=me)  # new Object
                scene.collection.objects.link(ob)  # link it to Scene Collection
                try:
                    ob.from_fds(context, fds_namelist=fds_namelist, free_text=free_text)
                except BFNotImported:
                    bpy.data.objects.remove(ob, do_unlink=True)
                else:
                    is_imported = True
            case t if t == Scene:
                try:
                    bf_namelist(scene).from_fds(
                        context, fds_namelist=fds_namelist, free_text=free_text
                    )
                except BFNotImported:
                    pass
                else:
                    is_imported = True
            case t if t == Material:
                ma = bpy.data.materials.new(hid)  # new Material
                try:
                    ma.from_fds(context, fds_namelist=fds_namelist, free_text=free_text)
                except BFNotImported:
                    bpy.data.materials.remove(ma, do_unlink=True)
                else:
                    ma.use_fake_user = True  # prevent del (eg. used by PART)
                    is_imported = True
            case _:
                raise AssertionError(f"Unhandled bf_namelist for <{fds_namelist}>") 
    if not is_imported:  # last resort, import to Free Text
        free_text.write(fds_namelist.to_fds(context) + "\n")


class BFScene:
    """!
    Extension of Blender Scene.
    """

    @property
    def bf_namelists(self):
        """!
        Return related bf_namelist, instance of BFNamelist.
        """
        return (n(self) for n in BFNamelist.subclasses if n.bpy_type == Scene)

    def to_fds(self, context, full=False, all_surfs=False, save=False, filepath=None):
        """!
        Return the FDS formatted string.
        @param context: the Blender context.
        @param full: if True, return full FDS case.
        @param all_surfs: if True, return all SURF namelists, even if not related.
        @param save: if True, save to disk.
        @param filepath: set case directory and name.
        @return FDS formatted string (eg. "&OBST ID='Test' /"), or None.
        """
        lines = list()

        # Set mysef as the right Scene instance in the context
        # It is needed, because context.scene is needed elsewhere
        bpy.context.window.scene = self  # set context.scene

        # Check and get scene name and dir from filepath
        if not bpy.data.is_saved:
            raise BFException(self, "Save the current Blender file before exporting.")
        if filepath:
            self.name, self.bf_config_directory = utils.io.os_filepath_to_bl(
                bpy.path.abspath(filepath)
            )

        # Header
        if full:
            v = sys.modules["blenderfds"].bl_info["version"]
            blv = bpy.app.version_string
            now = time.strftime("%a, %d %b %Y, %H:%M:%S", time.localtime())
            blend_filepath = bpy.data.filepath or "not saved"
            if len(blend_filepath) > 60:
                blend_filepath = "..." + blend_filepath[-57:]
            lines.extend(  # header has !!!
                (
                    f"!!! Generated by BlenderFDS {v[0]}.{v[1]}.{v[2]} on Blender {blv}",
                    f"!!! Date: <{now}>",
                    f"!!! File: <{blend_filepath}>",
                    f"! --- Case from Blender Scene <{self.name}> and View Layer <{context.view_layer.name}>",
                )
            )

        # Append Scene namelists
        lines.extend(
            bf_namelist.to_fds(context)
            for bf_namelist in self.bf_namelists
            if bf_namelist
        )

        # Append free text
        if self.bf_config_text:
            text = self.bf_config_text.as_string()
            if text:
                text = f"\n! --- Free text from Blender Text <{self.bf_config_text.name}>\n{text}"
                lines.append(text)

        # Append Material namelists
        if full:
            if all_surfs:
                header = "\n! --- Boundary conditions from all Blender Materials"
                mas = list(ma for ma in bpy.data.materials)  # all
            else:
                header = "\n! --- Boundary conditions from Blender Materials"
                mas = list(  # related to scene
                    set(
                        ms.material
                        for ob in self.objects
                        for ms in ob.material_slots
                        if ms.material
                    )
                )
            mas.sort(key=lambda k: k.name)  # alphabetic sorting by name
            ma_lines = list(ma.to_fds(context) for ma in mas)
            if any(ma_lines):
                lines.append(header)
                lines.extend(ma_lines)

        # Append Collections and their Objects
        if full:
            text = self.collection.to_fds(context)
            if text:
                lines.append("\n! --- Geometric namelists from Blender Collections")
                lines.append(text)

        # Append TAIL
        if full and self.bf_head_export:
            lines.append("\n&TAIL /\n ")

        # Write to file
        fds_text = "\n".join(l for l in lines if l)
        if save:
            filepath = utils.io.bl_path_to_os(
                bl_path=self.bf_config_directory or "//",
                name=self.name,
                extension=".fds",
            )
            utils.io.write_txt_file(filepath, fds_text)
        else:
            return fds_text

    def from_fds(self, context, filepath=None, f90=None):
        """!
        Set self.bf_namelists from FDSCase, on error raise BFException.
        @param context: the Blender context.
        @param filepath: filepath of FDS case to be imported.
        @param f90: FDS formatted string of namelists, eg. "&OBST ID='Test' /\n&TAIL /".
        """
        # Set mysef as the right Scene instance in the context
        # this is used by context.scene calls elsewhere
        bpy.context.window.scene = self

        # Init
        fds_case = FDSCase()
        fds_case.from_fds(filepath=filepath, f90=f90)
        # self.bf_config_directory = os.path.dirname(filepath)  # FIXME useful?

        # Prepare free text for unmanaged namelists
        free_text = bpy.data.texts.new(f"Imported text")
        self.bf_config_text = free_text

        # Import SURFs first to new materials
        _import_by_fds_label(fds_case=fds_case, fds_label="SURF", scene=self, context=context, free_text=free_text)

        # Import all MOVEs in a dict
        move_id_to_move = dict()
        for fds_namelist in fds_case.get("MOVE", remove=True):
            p_id = fds_namelist.get("ID")
            if not p_id:
                raise BFNotImported(None, "Missing ID: <{fds_namelist}>")
            move_id_to_move[p_id.value] = fds_namelist

        # Import OBSTs before VENTs
        _import_by_fds_label(fds_case=fds_case, fds_label="OBST", scene=self, context=context, free_text=free_text)

        # Import all other namelists to Object or Scene
        _import_by_fds_label(fds_case=fds_case, fds_label=None, scene=self, context=context, free_text=free_text)

        # Transform the Objects that have a MOVE_ID
        for ob in self.collection.objects:
            if ob.bf_move_id_export and ob.bf_move_id:
                if not ob.bf_move_id in move_id_to_move:
                    raise BFException(
                        self, f"Missing MOVE <{ob.bf_move_id}> in fds file"
                    )
                ON_MOVE(ob).from_fds(
                    context,
                    fds_namelist=move_id_to_move[ob.bf_move_id],
                    free_text=free_text,
                )

        # Set imported Scene visibility
        context.window.scene = self

        # Show free text
        free_text.current_line_index = 0
        bpy.ops.scene.bf_show_text()  # FIXME FIXME FIXME remove ops, put py

    @classmethod
    def register(cls):
        """!
        Register related Blender properties.
        @param cls: class to be registered.
        """
        Scene.bf_namelists = cls.bf_namelists
        Scene.to_fds = cls.to_fds
        Scene.from_fds = cls.from_fds
        Scene.bf_file_version = IntVectorProperty(
            name="BlenderFDS File Version", size=3
        )

    @classmethod
    def unregister(cls):
        """!
        Unregister related Blender properties.
        @param cls: class to be unregistered.
        """
        del Scene.bf_file_version
        del Scene.from_fds
        del Scene.to_fds
        del Scene.bf_namelists
