# This file is part of MyPaint.
# -*- coding: utf-8 -*-
# Copyright (C) 2007-2013 by Martin Renold <martinxyz@gmx.ch>
# Copyright (C) 2013-2015 by the MyPaint Development Team.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.


## Imports

# Know now that these are the rules of import.
# That we live by, by which we abide.
#
# Rule 1. You will not import mypaintlib before GTK and GLib.
# Rule 2. You **WILL NOT** import mypaintlib before GTK and GLib.
# Rule 2a. Not even to make the error message pretty in main.py or the
#          launch script.
# Rule 3. See rules 1 and 2.
# Rule 4. It's nice to divide the imports into blocks separated by
#         blank lines. Standard Python libs, then 3rd-party, then ours.

# Why the strict order for GLib/mypaintlib? For one thing, icon
# searching breaks unless Gtk is imported before mypaintlib on MSYS2's
# Windows-x86_64 (but not Windows-i686 or Linux-anything - go figure). I
# guess GTK is caching something internally, like GLib's g_get_*_dir()
# stuff, but wtf is libmypaint doing to break those?

import locale
import gettext
import os
import sys
from os.path import join
from collections import namedtuple
import logging
logger = logging.getLogger(__name__)

from gi.repository import GObject
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GdkPixbuf
from gi.repository import GLib
from gi.repository import Gio
from gettext import gettext as _

import lib.document
from lib import brush
from lib import helpers
from lib import mypaintlib
from libmypaint import brushsettings
import gui.device
import filehandling
import keyboard
import brushmanager
import windowing
import document
import tileddrawwidget
import workspace
import topbar
import drawwindow
import backgroundwindow
import preferenceswindow
import brusheditor
import layerswindow
import previewwindow
import optionspanel
import framewindow
import scratchwindow
import inputtestwindow
import brushiconeditor
import history
import colortools
import brushmodifier
import toolbar
import linemode
import colors
import colorpreview
import fill
from brushcolor import BrushColorManager
from overlays import LastPaintPosOverlay
from overlays import ScaleOverlay
from buttonmap import ButtonMapping
import lib.glib
import gui.cursor
import lib.fileutils
import gui.picker
import gui.factoryaction  # registration only
import gui.autorecover


## Utility methods


def get_app():
    """Returns the `gui.application.Application` singleton object."""
    # Define this up front: gui.* requires the singleton object pretty much
    # everywhere, and the app instance carries them as members.
    return Application._INSTANCE


def _init_icons(icon_path, default_icon='mypaint'):
    """Set the icon theme search path, and GTK default window icon"""
    # Default location for our icons. The user's theme can override these.
    icon_theme = Gtk.IconTheme.get_default()
    icon_theme.append_search_path(icon_path)
    # Ensure that MyPaint has its icons.
    # Test a sample symbolic icon to make sure librsvg is installed and
    # GdkPixbuf's loader cache has been informed about it.
    icons_missing = False
    icon_tests = [
        (
            default_icon,
            "check that mypaint icons have been installed "
            "into {}".format(icon_path),
        ), (
            "mypaint-brush-symbolic",
            "check that librsvg is installed, and update loaders.cache",
        ),
    ]
    for icon_name, missing_msg in icon_tests:
        try:
            pixbuf = icon_theme.load_icon(icon_name, 32, 0)
        except:
            logger.exception("Missing icon %r: %s", icon_name, missing_msg)
            icons_missing = True
    if icons_missing:
        logger.critical("Required icon(s) missing")
        logger.error('Icon search path: %r', icon_theme.get_search_path())
        logger.error(
            "Mypaint can't run sensibly without its icons; "
            "please check your installation. See "
            "https://gna.org/bugs/?18460 for possible solutions."
        )
        logger.error
        sys.exit(1)
    # Default icon for all windows
    Gtk.Window.set_default_icon_name(default_icon)


## Class definitions

_STATEDIRS_FIELDS = (
    "app_data",
    "app_icons",
    "user_data",
    "user_config",
)


class StateDirs (namedtuple("StateDirs", _STATEDIRS_FIELDS)):
    """Where MyPaint stores its config, read-only data etc.

    This caches some special paths that will never change for the
    lifetime of the application. An instance resides in the main
    Application object as `app.state_dirs`.

    :ivar unicode app_data:
        App-specific read-only data area.
        Path used for UI definition XML, and the default sets of
        backgrounds, palettes, and brush defintions.
        Often $PREFIX/share/.
    :ivar unicode app_icons:
        Extra search path for read-only themeable UI icons.
        This will be used in addition to $XDG_DATA_DIRS for the purposes of
        icon lookup. Normally it's $PREFIX/share/icons.
    :ivar unicode user_data:
        Read-write location of the user's app-specific data.
        For MyPaint, this means the user's brushes, backgrounds, and
        scratchpads. Commonly $XDG_DATA_HOME/mypaint, i.e.
        ~/.local/share/mypaint
    :ivar unicode user_config:
        Location of the user's app-specific config area.
        This is where MyPaint will save user preferences data and the
        keyboard accelerator map.
        Commonly $XDG_CONFIG_HOME/mypaint, i.e. ~/.config/mypaint

    """


class Application (object):
    """Main application singleton.

    This class serves as a global container for everything that needs
    to be shared in the GUI. Its constructor is the last part of the
    initialization, called by main.py or by the testing scripts.

    Access via `gui.application.get_app()`.

    """

    #: Singleton instance
    _INSTANCE = None

    def __init__(self, filenames, state_dirs, version, fullscreen=False):
        """Construct, but do not run.

        :param list filenames: The list of files to load (unicode required)
        :param StateDirs state_dirs: static special paths.
        :param unicode version: Version string for the about dialog.
        :param bool fullscreen: Go fullscreen after starting.

        Only the first filename listed will be loaded. If no files are
        listed, the autosave recovery dialog may be shown when the
        application starts up.

        """
        assert Application._INSTANCE is None
        super(Application, self).__init__()
        Application._INSTANCE = self

        self.state_dirs = state_dirs  #: Static special paths: see StateDirs

        self.version = version  #: version string for the app.

        # Create the user's config directory and any needed R/W data
        # storage areas.
        for basedir in [state_dirs.user_config, state_dirs.user_data]:
            if not os.path.isdir(basedir):
                os.mkdir(basedir)
                logger.info('Created basedir %r', basedir)
        for datasubdir in [u'backgrounds', u'brushes', u'scratchpads']:
            datadir = os.path.join(state_dirs.user_data, datasubdir)
            if not os.path.isdir(datadir):
                os.mkdir(datadir)
                logger.info('Created data subdir %r', datadir)

        _init_icons(state_dirs.app_icons)

        # Core actions and menu structure
        resources_xml = join(self.datapath, "gui", "resources.xml")
        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mypaint")
        self.builder.add_from_file(resources_xml)

        self.ui_manager = self.builder.get_object("app_ui_manager")
        signal_callback_objs = [self]

        Gdk.set_program_class('MyPaint')

        self.pixmaps = PixbufDirectory(join(state_dirs.app_data, u'pixmaps'))
        self.cursor_color_picker = Gdk.Cursor.new_from_pixbuf(
            Gdk.Display.get_default(),
            self.pixmaps.cursor_color_picker,
            3, 15,
        )
        self.cursors = gui.cursor.CustomCursorMaker(self)

        # unmanaged main brush; always the same instance (we can attach settings_observers)
        # this brush is where temporary changes (color, size...) happen
        self.brush = brush.BrushInfo()
        self.brush.load_defaults()

        # Global pressure mapping function, ignored unless set
        self.pressure_mapping = None

        self.preferences = {}
        self.load_settings()

        # Keyboard manager
        self.kbm = keyboard.KeyboardManager(self)

        # File I/O
        self.filehandler = filehandling.FileHandler(self)

        # Picking grabs
        self.context_grab = gui.picker.ContextPickingGrabPresenter()
        self.context_grab.app = self
        self.color_grab = gui.picker.ColorPickingGrabPresenter()
        self.color_grab.app = self

        # Load the main interface
        mypaint_main_xml = join(self.datapath, "gui", "mypaint.glade")
        self.builder.add_from_file(mypaint_main_xml)

        # Main drawing window
        self.drawWindow = self.builder.get_object("drawwindow")
        signal_callback_objs.append(self.drawWindow)

        # Workspace widget. Manages layout of toolwindows, and autohide in
        # fullscreen.
        workspace = self.builder.get_object("app_workspace")
        workspace.build_from_layout(self.preferences["workspace.layout"])
        workspace.floating_window_created += self._floating_window_created_cb
        fs_autohide_action = self.builder.get_object("FullscreenAutohide")
        fs_autohide_action.set_active(workspace.autohide_enabled)
        self.workspace = workspace

        # Working document: viewer widget
        app_canvas = self.builder.get_object("app_canvas")

        # Working document: model and controller
        model = lib.document.Document(self.brush)
        self.doc = document.Document(self, app_canvas, model)
        app_canvas.set_model(model)

        signal_callback_objs.append(self.doc)
        signal_callback_objs.append(self.doc.modes)

        self.scratchpad_filename = ""
        scratchpad_model = lib.document.Document(self.brush, painting_only=True)
        scratchpad_tdw = tileddrawwidget.TiledDrawWidget()
        scratchpad_tdw.set_model(scratchpad_model)
        self.scratchpad_doc = document.Document(self, scratchpad_tdw,
                                                scratchpad_model)
        self.brushmanager = brushmanager.BrushManager(
            join(self.state_dirs.app_data, 'brushes'),
            join(self.state_dirs.user_data, 'brushes'),
            self,
        )
        signal_callback_objs.append(self.filehandler)
        self.brushmodifier = brushmodifier.BrushModifier(self)
        signal_callback_objs.append(self.brushmodifier)
        self.line_mode_settings = linemode.LineModeSettings(self)

        # Button press mapping
        self.button_mapping = ButtonMapping()

        # Monitors pluggings and uses of input device, configures them,
        # and switches between device-specific brushes.
        self.device_monitor = gui.device.Monitor(self)

        if not self.preferences.get("scratchpad.last_opened_scratchpad", None):
            self.preferences["scratchpad.last_opened_scratchpad"] = self.filehandler.get_scratchpad_autosave()
        self.scratchpad_filename = self.preferences["scratchpad.last_opened_scratchpad"]

        self.brush_color_manager = BrushColorManager(self)
        self.brush_color_manager.set_picker_cursor(self.cursor_color_picker)
        self.brush_color_manager.set_data_path(self.datapath)

        #: Mapping of setting cname to a GtkAdjustment which controls the base
        #: value of that setting for the app's current brush.
        self.brush_adjustment = {}
        self.init_brush_adjustments()

        # Connect signals defined in resources.xml
        callback_finder = CallbackFinder(signal_callback_objs)
        self.builder.connect_signals(callback_finder)

        self.kbm.start_listening()
        self.filehandler.doc = self.doc
        self.filehandler.filename = None
        Gtk.AccelMap.load(join(self.user_confpath, 'accelmap.conf'))

        # Load the default background image if one exists
        layer_stack = self.doc.model.layer_stack
        inited_background = False
        for datapath in [self.user_datapath, self.datapath]:
            bg_path = join(datapath, backgroundwindow.BACKGROUNDS_SUBDIR,
                           backgroundwindow.DEFAULT_BACKGROUND)
            if not os.path.exists(bg_path):
                continue
            bg, errors = backgroundwindow.load_background(bg_path)
            if bg:
                layer_stack.set_background(bg, make_default=True)
                inited_background = True
                logger.info("Initialized background from %r", bg_path)
                break
            else:
                logger.warning(
                    "Failed to load user's default background image %r",
                    bg_path,
                )
                if errors:
                    for error in errors:
                        logger.warning("warning: %r", error)

        # Otherwise, try to use a sensible fallback background image.
        if not inited_background:
            bg_path = join(self.datapath, backgroundwindow.BACKGROUNDS_SUBDIR,
                           backgroundwindow.FALLBACK_BACKGROUND)
            bg, errors = backgroundwindow.load_background(bg_path)
            if bg:
                layer_stack.set_background(bg, make_default=True)
                inited_background = True
                logger.info("Initialized background from %r", bg_path)
            else:
                logger.warning(
                    "Failed to load fallback background image %r",
                    bg_path,
                )
                if errors:
                    for error in errors:
                        logger.warning("warning: %r", error)

        # Double fallback. Just use a color.
        if not inited_background:
            bg_color = (0xa8, 0xa4, 0x98)
            layer_stack.set_background(bg_color, make_default=True)
            logger.info("Initialized background to %r", bg_color)
            inited_background = True

        # Non-dockable subwindows
        # Loading is deferred as late as possible
        self._subwindow_classes = {
            # action-name: action-class
            "BackgroundWindow": backgroundwindow.BackgroundWindow,
            "BrushEditorWindow": brusheditor.BrushEditorWindow,
            "PreferencesWindow": preferenceswindow.PreferencesWindow,
            "InputTestWindow": inputtestwindow.InputTestWindow,
            "BrushIconEditorWindow": brushiconeditor.BrushIconEditorWindow,
            }
        self._subwindows = {}

        # Statusbar init
        statusbar = self.builder.get_object("app_statusbar")
        self.statusbar = statusbar
        context_id = statusbar.get_context_id("transient-message")
        self._transient_msg_context_id = context_id
        self._transient_msg_remove_timeout_id = None

        # Show main UI.
        self.drawWindow.show_all()
        GObject.idle_add(self._at_application_start, filenames, fullscreen)

    def _at_application_start(self, filenames, fullscreen):
        col = self.brush_color_manager.get_color()
        self.brushmanager.select_initial_brush()
        self.brush_color_manager.set_color(col)
        if filenames:
            # Open only the first file, no matter how many has been specified
            # If the file does not exist just set it as the file to save to
            fn = filenames[0]
            if not os.path.exists(fn):
                self.filehandler.filename = fn
            else:
                self.filehandler.open_file(fn)

        # Load last scratchpad
        sp_autosave_key = "scratchpad.last_opened_scratchpad"
        autosave_name = self.preferences[sp_autosave_key]
        if not autosave_name:
            autosave_name = self.filehandler.get_scratchpad_autosave()
            self.preferences[sp_autosave_key] = autosave_name
            self.scratchpad_filename = autosave_name
        if os.path.isfile(autosave_name):
            try:
                self.filehandler.open_scratchpad(autosave_name)
            except AttributeError:
                pass

        self.apply_settings()
        self.drawWindow.present()

        # Handle fullscreen command line option
        if fullscreen:
            self.drawWindow.fullscreen_cb()

        if not filenames:
            autosave_recovery = gui.autorecover.Presenter(self)
            autosave_recovery.run()

    def save_settings(self):
        """Saves the current settings to persistent storage."""
        self.brushmanager.save_brushes_for_devices()
        self.brushmanager.save_brush_history()
        self.filehandler.save_scratchpad(self.scratchpad_filename)
        settingspath = join(self.user_confpath, 'settings.json')
        jsonstr = helpers.json_dumps(self.preferences)
        f = open(settingspath, 'w')
        f.write(jsonstr)
        f.close()

    def apply_settings(self):
        """Applies the current settings.

        Called at startup and from the prefs dialog.
        """
        self._apply_pressure_mapping_settings()
        self._apply_button_mapping_settings()
        self._apply_autosave_settings()
        self.preferences_window.update_ui()

    def load_settings(self):
        """Loads the settings from persistent storage.

        Uses defaults if not explicitly configured.

        """
        def get_json_config():
            settingspath = join(self.user_confpath, 'settings.json')
            jsonstr = open(settingspath).read()
            try:
                return helpers.json_loads(jsonstr)
            except Exception, e:
                logger.warning("settings.json: %s", str(e))
                logger.warning("Failed to load settings: using defaults")
                return {}
        if sys.platform == 'win32':
            ud_docs = lib.glib.get_user_special_dir(
                GLib.UserDirectory.DIRECTORY_DOCUMENTS,
            ).decode("utf-8")
            scrappre = os.path.join(ud_docs, u'MyPaint', u'scrap')
        else:
            scrappre = u'~/MyPaint/scrap'
        DEFAULT_CONFIG = {
            'saving.scrap_prefix': scrappre,
            'input.device_mode': 'screen',
            'input.global_pressure_mapping': [(0.0, 1.0), (1.0, 0.0)],
            'view.default_zoom': 1.0,
            'view.real_alpha_checks': True,
            'ui.hide_menubar_in_fullscreen': True,
            'ui.hide_toolbar_in_fullscreen': True,
            'ui.hide_subwindows_in_fullscreen': True,
            'ui.parts': dict(main_toolbar=True, menubar=True),
            'ui.feedback.scale': False,
            'ui.feedback.last_pos': False,
            'ui.toolbar_items': dict(
                toolbar1_file=True,
                toolbar1_scrap=False,
                toolbar1_edit=True,
                toolbar1_blendmodes=False,
                toolbar1_linemodes=True,
                toolbar1_view_modes=True,
                toolbar1_view_manips=False,
                toolbar1_view_resets=True,
            ),
            'ui.toolbar_icon_size': 'large',
            'ui.dark_theme_variant': True,
            'saving.default_format': 'openraster',
            'brushmanager.selected_brush': None,
            'brushmanager.selected_groups': [],
            'frame.color_rgba': (0.12, 0.12, 0.12, 0.92),
            'misc.context_restores_color': True,

            'document.autosave_backups': True,
            'document.autosave_interval': 10,

            'display.colorspace': "srgb",
            # sRGB is a good default even for OS X since v10.6 / Snow
            # Leopard: http://support.apple.com/en-us/HT3712.
            # Version 10.6 was released in September 2009.

            "scratchpad.last_opened_scratchpad": "",

            # Initial main window positions
            "workspace.layout": {
                "position": dict(x=50, y=32, w=-50, h=-100),
                "autohide": True,
            },

            # Linux defaults.
            # Alt is the normal window resizing/moving key these days,
            # so provide a Ctrl-based equivalent for all alt actions.
            'input.button_mapping': {
                # Note that space is treated as a fake Button2
                '<Shift>Button1':          'StraightMode',
                '<Control>Button1':        'ColorPickMode',
                '<Alt>Button1':            'ColorPickMode',
                'Button2':                 'PanViewMode',
                '<Shift>Button2':          'RotateViewMode',
                '<Control>Button2':        'ZoomViewMode',
                '<Alt>Button2':            'ZoomViewMode',
                '<Control><Shift>Button2': 'FrameEditMode',
                '<Alt><Shift>Button2':     'FrameEditMode',
                'Button3':                 'ShowPopupMenu',
            },
        }
        if sys.platform == 'win32':
            # The Linux wacom driver inverts the button numbers of the
            # pen flip button, because middle-click is the more useful
            # action on Linux. However one of the two buttons is often
            # accidentally hit with the thumb while painting. We want
            # to assign panning to this button by default.
            linux_mapping = DEFAULT_CONFIG["input.button_mapping"]
            DEFAULT_CONFIG["input.button_mapping"] = {}
            for bp, actname in linux_mapping.iteritems():
                bp = bp.replace("Button2", "ButtonTMP")
                bp = bp.replace("Button3", "Button2")
                bp = bp.replace("ButtonTMP", "Button3")
                DEFAULT_CONFIG["input.button_mapping"][bp] = actname

        self.preferences = DEFAULT_CONFIG.copy()
        try:
            user_config = get_json_config()
        except IOError:
            user_config = {}
        self.preferences.update(user_config)
        if 'ColorPickerPopup' in self.preferences["input.button_mapping"].values():
            # old config file; users who never assigned any buttons would
            # end up with Ctrl-Click color picker broken after upgrade
            self.preferences["input.button_mapping"] = DEFAULT_CONFIG["input.button_mapping"]

    def add_action_group(self, ag):
        self.ui_manager.insert_action_group(ag, -1)

    def find_action(self, name):
        for ag in self.ui_manager.get_action_groups():
            result = ag.get_action(name)
            if result is not None:
                return result

    ## Brush settings: GtkAdjustments for base values

    def init_brush_adjustments(self):
        """Initializes the base value adjustments for all brush settings"""
        assert not self.brush_adjustment
        changed_cb = self._brush_adjustment_value_changed_cb
        for s in brushsettings.settings_visible:
            adj = Gtk.Adjustment(value=s.default, lower=s.min, upper=s.max,
                                 step_incr=0.01, page_incr=0.1)
            self.brush_adjustment[s.cname] = adj
            adj.connect("value-changed", changed_cb, s.cname)
        self.brush.observers.append(self._brush_modified_cb)

    def _brush_adjustment_value_changed_cb(self, adj, cname):
        """Updates a brush setting when the user tweaks it using a scale"""
        newvalue = adj.get_value()
        if self.brush.get_base_value(cname) != newvalue:
            self.brush.set_base_value(cname, newvalue)

    def _brush_modified_cb(self, settings):
        """Updates the brush's base setting adjustments on brush changes"""
        for cname in settings:
            adj = self.brush_adjustment.get(cname, None)
            if adj is None:
                continue
            value = self.brush.get_base_value(cname)
            adj.set_value(value)

    ## Button mappings, global pressure curve

    def _apply_button_mapping_settings(self):
        self.button_mapping.update(self.preferences["input.button_mapping"])

    def _apply_pressure_mapping_settings(self):
        p = self.preferences['input.global_pressure_mapping']
        if len(p) == 2 and abs(p[0][1]-1.0)+abs(p[1][1]-0.0) < 0.0001:
            # 1:1 mapping (mapping disabled)
            self.pressure_mapping = None
        else:
            # This mapping is still required for certain problematic hw
            # See https://github.com/mypaint/mypaint/issues/275
            m = mypaintlib.MappingWrapper(1)
            m.set_n(0, len(p))
            for i, (x, y) in enumerate(p):
                m.set_point(0, i, x, 1.0-y)

            def mapping(pressure):
                return m.calculate_single_input(pressure)
            self.pressure_mapping = mapping

    def _apply_autosave_settings(self):
        active = self.preferences["document.autosave_backups"]
        interval = self.preferences["document.autosave_interval"]
        logger.debug(
            "Applying autosave settings: active=%r, interval=%r",
            active, interval,
        )
        model = self.doc.model
        model.autosave_backups = active
        model.autosave_interval = interval

    def save_gui_config(self):
        Gtk.AccelMap.save(join(self.user_confpath, 'accelmap.conf'))
        workspace = self.workspace
        self.preferences["workspace.layout"] = workspace.get_layout()
        self.save_settings()

    def message_dialog(self, text, type=Gtk.MessageType.INFO, flags=0,
                       secondary_text=None, long_text=None, title=None,
                       investigate_dir=None, investigate_str=None):
        """Utility function to show a message/information dialog"""
        d = Gtk.MessageDialog(
            parent=self.drawWindow,
            flags=flags,
            type=type,
            buttons=[],
        )
        # Auxiliary actions first...
        if investigate_dir and os.path.isdir(investigate_dir):
            if not investigate_str:
                tmpl = _(u"Open Folder “{folder_basename}”…")
                investigate_str = tmpl.format(
                    folder_basename = os.path.basename(investigate_dir),
                )
            d.add_button(investigate_str, -1)
        # ... so that the main actions end up in the bottom-right of the
        # dialog (reversed for rtl scripts), where the eye ends up
        # naturally at the end of the flow.
        d.add_button(_("OK"), Gtk.ResponseType.OK)
        d.set_markup(text)
        if title is not None:
            d.set_title(title)
        if secondary_text is not None:
            d.format_secondary_markup(secondary_text)
        if long_text is not None:
            buf = Gtk.TextBuffer()
            buf.set_text(long_text)
            tv = Gtk.TextView.new_with_buffer(buf)
            tv.show()
            tv.set_editable(False)
            tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            scrolls = Gtk.ScrolledWindow()
            scrolls.show()
            scrolls.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
            scrolls.add(tv)
            scrolls.set_size_request(-1, 300)
            scrolls.set_shadow_type(Gtk.ShadowType.IN)
            d.get_message_area().pack_start(scrolls, True, True, 0)
        response = d.run()
        d.destroy()
        if response == -1:
            lib.fileutils.startfile(investigate_dir, "open")

    def show_transient_message(self, text, seconds=5):
        """Display a brief, impermanent status message"""
        context_id = self._transient_msg_context_id
        self.statusbar.remove_all(context_id)
        self.statusbar.push(context_id, text)
        timeout_id = self._transient_msg_remove_timeout_id
        if timeout_id is not None:
            GLib.source_remove(timeout_id)
        timeout_id = GLib.timeout_add_seconds(
            interval=seconds,
            function=self._transient_msg_remove_timer_cb,
            )
        self._transient_msg_remove_timeout_id = timeout_id

    def _transient_msg_remove_timer_cb(self, *_ignored):
        context_id = self._transient_msg_context_id
        self.statusbar.remove_all(context_id)
        self._transient_msg_remove_timeout_id = None
        return False

    def pick_color_at_pointer(self, widget, size=3):
        """Set the brush color from the current pointer position on screen.

        This is a wrapper for `gui.colors.get_color_at_pointer()`, and
        additionally sets the current brush color.

        """
        # Due to a performance bug, color picking can take more time
        # than we have between two motion events (about 8ms).
        if hasattr(self, 'delayed_color_pick_id'):
            GObject.source_remove(self.delayed_color_pick_id)

        def delayed_color_pick():
            del self.delayed_color_pick_id
            color = colors.get_color_at_pointer(widget.get_display(), size)
            self.brush_color_manager.set_color(color)

        self.delayed_color_pick_id = GObject.idle_add(delayed_color_pick)

    ## Subwindows

    @property
    def background_window(self):
        """The background switcher subwindow."""
        return self.get_subwindow("BackgroundWindow")

    @property
    def brush_settings_window(self):
        """The brush settings editor subwindow."""
        return self.get_subwindow("BrushSettingsWindow")

    @property
    def brush_icon_editor_window(self):
        """The brush icon editor subwindow."""
        return self.get_subwindow("BrushIconEditorWindow")

    @property
    def brush_icon_editor_window(self):
        """The brush editor subwindow."""
        return self.get_subwindow("BrushEditorWindow")

    @property
    def preferences_window(self):
        """The preferences subwindow."""
        return self.get_subwindow("PreferencesWindow")

    @property
    def input_test_window(self):
        """The input test window."""
        return self.get_subwindow("InputTestWindow")

    def get_subwindow(self, name):
        """Get a subwindow by its name."""
        if name in self._subwindows:
            window = self._subwindows[name]
        elif name in self._subwindow_classes:
            window_class = self._subwindow_classes[name]
            window = window_class()
            window.__toggle_action = self.find_action(name)
            window.connect("hide", self._subwindow_hide_cb)
            self._subwindows[name] = window
        else:
            raise ValueError("Unkown subwindow %r" % name)
        return window

    def has_subwindow(self, name):
        """True if the named subwindow is known."""
        return name in self._subwindow_classes

    def _subwindow_hide_cb(self, subwindow):
        """Toggles off a subwindow's related action when it's hidden."""
        action = subwindow.__toggle_action
        if action and action.get_active():
            action.set_active(False)

    def autorecover_cb(self, action):
        autosave_recovery = gui.autorecover.Presenter(self)
        autosave_recovery.run(no_autosaves_dialog=True)

    ## Workspace callbacks

    def _floating_window_created_cb(self, workspace, floatwin):
        """Adds newly created `workspace.ToolStackWindow`s to the kbm."""
        self.kbm.add_window(floatwin)


    ## Stroke loading support

    # App-wide, while the single painting brush still lives here.

    def restore_brush_from_stroke_info(self, strokeinfo):
        """Restores the app brush from a stroke

        :param strokeinfo: Stroke details from the stroke map
        :type strokeinfo: lib.strokemap.StrokeShape
        """
        mb = brushmanager.ManagedBrush(self.brushmanager)
        mb.brushinfo.load_from_string(strokeinfo.brush_string)
        self.brushmanager.select_brush(mb)
        self.brushmodifier.restore_context_of_selected_brush()

    ## Compatibility properties for special unchanging paths

    @property
    def user_confpath(self):
        """Dir for read/write user configs (prefer app.paths.user_config)."""
        return self.state_dirs.user_config

    @property
    def user_datapath(self):
        """Dir for read/write user data (prefer app.paths.user_data)."""
        return self.state_dirs.user_data

    @property
    def datapath(self):
        """Dir holding read-only app data (prefer app.paths.app_data)."""
        return self.state_dirs.app_data


class PixbufDirectory (object):

    def __init__(self, dirname):
        super(PixbufDirectory, self).__init__()
        self.dirname = dirname
        self.cache = {}

    def __getattr__(self, name):
        if name not in self.cache:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(join(self.dirname, name + '.png'))
            except GObject.GError, e:
                raise AttributeError(str(e))
            self.cache[name] = pixbuf
        return self.cache[name]


class CallbackFinder (object):
    """Finds callbacks amongst a list of objects.

    It's not possible to call `GtkBuilder.connect_signals()` more than once,
    but we use more tnan one backend object. Thus, this little workaround is
    necessary during construction.

    See http://stackoverflow.com/questions/4637792

    """

    def __init__(self, objects):
        super(CallbackFinder, self).__init__()
        self._objs = list(objects)

    def __getattr__(self, name):
        name = str(name)
        found = [getattr(obj, name) for obj in self._objs
                 if hasattr(obj, name)]
        if len(found) == 1:
            return found[0]
        elif len(found) > 1:
            logger.warning("ambiguity: %r resolves to %r", name, found)
            logger.warning("using first match only.")
            return found[0]
        else:
            raise AttributeError(
                "No method named %r was defined on any of %r"
                % (name, self._objs))
