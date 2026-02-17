"""Browser: tree, items at path, find by URI, load instrument/effect."""

from __future__ import absolute_import, print_function, unicode_literals

import traceback


def find_browser_item_by_uri(
    browser_or_item, uri, max_depth=10, current_depth=0, ctrl=None
):
    """Find a browser item by its URI."""
    try:
        if hasattr(browser_or_item, "uri") and browser_or_item.uri == uri:
            return browser_or_item
        if current_depth >= max_depth:
            return None
        if hasattr(browser_or_item, "instruments"):
            categories = [
                browser_or_item.instruments,
                browser_or_item.sounds,
                browser_or_item.drums,
                browser_or_item.audio_effects,
                browser_or_item.midi_effects,
                browser_or_item.plugins,
            ]
            for category in categories:
                item = find_browser_item_by_uri(
                    category, uri, max_depth, current_depth + 1, ctrl
                )
                if item:
                    return item
            return None
        if hasattr(browser_or_item, "children") and browser_or_item.children:
            for child in browser_or_item.children:
                item = find_browser_item_by_uri(
                    child, uri, max_depth, current_depth + 1, ctrl
                )
                if item:
                    return item
        return None
    except Exception as e:
        if ctrl:
            ctrl.log_message(
                "Error finding browser item by URI: {0}".format(str(e))
            )
        return None


def get_browser_item(song, uri, path, ctrl=None):
    """Get a browser item by URI or path."""
    try:
        if ctrl is None:
            raise RuntimeError("get_browser_item requires ctrl for application()")
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")
        result = {"uri": uri, "path": path, "found": False}
        if uri:
            item = find_browser_item_by_uri(app.browser, uri, ctrl=ctrl)
            if item:
                result["found"] = True
                result["item"] = {
                    "name": item.name,
                    "is_folder": item.is_folder,
                    "is_device": item.is_device,
                    "is_loadable": item.is_loadable,
                    "uri": item.uri,
                }
                return result
        if path:
            path_parts = path.split("/")
            current_item = None
            if path_parts[0].lower() == "instruments":
                current_item = app.browser.instruments
            elif path_parts[0].lower() == "sounds":
                current_item = app.browser.sounds
            elif path_parts[0].lower() == "drums":
                current_item = app.browser.drums
            elif path_parts[0].lower() == "audio_effects":
                current_item = app.browser.audio_effects
            elif path_parts[0].lower() == "midi_effects":
                current_item = app.browser.midi_effects
            elif path_parts[0].lower() == "plugins":
                current_item = app.browser.plugins
            else:
                current_item = app.browser.instruments
                path_parts = ["instruments"] + path_parts
            for i in range(1, len(path_parts)):
                part = path_parts[i]
                if not part:
                    continue
                found = False
                for child in current_item.children:
                    if child.name.lower() == part.lower():
                        current_item = child
                        found = True
                        break
                if not found:
                    result["error"] = "Path part '{0}' not found".format(part)
                    return result
            result["found"] = True
            result["item"] = {
                "name": current_item.name,
                "is_folder": current_item.is_folder,
                "is_device": current_item.is_device,
                "is_loadable": current_item.is_loadable,
                "uri": current_item.uri,
            }
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting browser item: " + str(e))
            ctrl.log_message(traceback.format_exc())
        raise


def load_browser_item(song, track_index, item_uri, ctrl=None):
    """Load a browser item onto a track by its URI."""
    try:
        if track_index < 0 or track_index >= len(song.tracks):
            raise IndexError("Track index out of range")
        track = song.tracks[track_index]
        if ctrl is None:
            raise RuntimeError(
                "load_browser_item requires ctrl for application()"
            )
        app = ctrl.application()
        item = find_browser_item_by_uri(app.browser, item_uri, ctrl=ctrl)
        if not item:
            raise ValueError(
                "Browser item with URI '{0}' not found".format(item_uri)
            )
        song.view.selected_track = track
        app.browser.load_item(item)
        return {
            "loaded": True,
            "item_name": item.name,
            "track_name": track.name,
            "uri": item_uri,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message(
                "Error loading browser item: {0}".format(str(e))
            )
            ctrl.log_message(traceback.format_exc())
        raise


def load_instrument_or_effect(song, track_index, uri, ctrl=None):
    """Load an instrument or effect onto a track by URI (alias for load_browser_item)."""
    return load_browser_item(song, track_index, uri, ctrl)


def load_on_return_track(song, return_index, uri, ctrl=None):
    """Load an instrument or effect onto a return track by URI."""
    try:
        if return_index < 0 or return_index >= len(song.return_tracks):
            raise IndexError("Return track index out of range")
        track = song.return_tracks[return_index]
        if ctrl is None:
            raise RuntimeError(
                "load_on_return_track requires ctrl for application()"
            )
        app = ctrl.application()
        item = find_browser_item_by_uri(app.browser, uri, ctrl=ctrl)
        if not item:
            raise ValueError(
                "Browser item with URI '{0}' not found".format(uri)
            )
        song.view.selected_track = track
        app.browser.load_item(item)
        return {
            "loaded": True,
            "item_name": item.name,
            "track_name": track.name,
            "return_index": return_index,
            "uri": uri,
        }
    except Exception as e:
        if ctrl:
            ctrl.log_message(
                "Error loading on return track: {0}".format(str(e))
            )
            ctrl.log_message(traceback.format_exc())
        raise


def _process_item(item):
    """Build a dict for a browser item (no children recursion)."""
    if not item:
        return None
    return {
        "name": item.name if hasattr(item, "name") else "Unknown",
        "is_folder": hasattr(item, "children") and bool(item.children),
        "is_device": hasattr(item, "is_device") and item.is_device,
        "is_loadable": hasattr(item, "is_loadable") and item.is_loadable,
        "uri": item.uri if hasattr(item, "uri") else None,
        "children": [],
    }


def get_browser_tree(song, category_type, ctrl=None):
    """Get a simplified tree of browser categories."""
    try:
        if ctrl is None:
            raise RuntimeError(
                "get_browser_tree requires ctrl for application()"
            )
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")
        if not hasattr(app, "browser") or app.browser is None:
            raise RuntimeError(
                "Browser is not available in the Live application"
            )
        browser_attrs = [
            attr
            for attr in dir(app.browser)
            if not attr.startswith("_")
        ]
        if ctrl:
            ctrl.log_message(
                "Available browser attributes: {0}".format(browser_attrs)
            )
        result = {
            "type": category_type,
            "categories": [],
            "available_categories": browser_attrs,
        }
        if (category_type == "all" or category_type == "instruments") and hasattr(
            app.browser, "instruments"
        ):
            try:
                instruments = _process_item(app.browser.instruments)
                if instruments:
                    instruments["name"] = "Instruments"
                    result["categories"].append(instruments)
            except Exception as e:
                if ctrl:
                    ctrl.log_message(
                        "Error processing instruments: {0}".format(str(e))
                    )
        if (category_type == "all" or category_type == "sounds") and hasattr(
            app.browser, "sounds"
        ):
            try:
                sounds = _process_item(app.browser.sounds)
                if sounds:
                    sounds["name"] = "Sounds"
                    result["categories"].append(sounds)
            except Exception as e:
                if ctrl:
                    ctrl.log_message(
                        "Error processing sounds: {0}".format(str(e))
                    )
        if (category_type == "all" or category_type == "drums") and hasattr(
            app.browser, "drums"
        ):
            try:
                drums = _process_item(app.browser.drums)
                if drums:
                    drums["name"] = "Drums"
                    result["categories"].append(drums)
            except Exception as e:
                if ctrl:
                    ctrl.log_message(
                        "Error processing drums: {0}".format(str(e))
                    )
        if (
            category_type == "all"
            or category_type == "audio_effects"
        ) and hasattr(app.browser, "audio_effects"):
            try:
                audio_effects = _process_item(app.browser.audio_effects)
                if audio_effects:
                    audio_effects["name"] = "Audio Effects"
                    result["categories"].append(audio_effects)
            except Exception as e:
                if ctrl:
                    ctrl.log_message(
                        "Error processing audio_effects: {0}".format(str(e))
                    )
        if (
            category_type == "all"
            or category_type == "midi_effects"
        ) and hasattr(app.browser, "midi_effects"):
            try:
                midi_effects = _process_item(app.browser.midi_effects)
                if midi_effects:
                    midi_effects["name"] = "MIDI Effects"
                    result["categories"].append(midi_effects)
            except Exception as e:
                if ctrl:
                    ctrl.log_message(
                        "Error processing midi_effects: {0}".format(str(e))
                    )
        for attr in browser_attrs:
            if attr not in (
                "instruments",
                "sounds",
                "drums",
                "audio_effects",
                "midi_effects",
            ) and (category_type == "all" or category_type == attr):
                try:
                    item = getattr(app.browser, attr)
                    if hasattr(item, "children") or hasattr(item, "name"):
                        category = _process_item(item)
                        if category:
                            category["name"] = attr.capitalize()
                            result["categories"].append(category)
                except Exception as e:
                    if ctrl:
                        ctrl.log_message(
                            "Error processing {0}: {1}".format(attr, str(e))
                        )
        if ctrl:
            ctrl.log_message(
                "Browser tree generated for {0} with {1} root categories".format(
                    category_type, len(result["categories"])
                )
            )
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message("Error getting browser tree: {0}".format(str(e)))
            ctrl.log_message(traceback.format_exc())
        raise


def get_browser_items_at_path(song, path, ctrl=None):
    """Get browser items at a specific path."""
    try:
        if ctrl is None:
            raise RuntimeError(
                "get_browser_items_at_path requires ctrl for application()"
            )
        app = ctrl.application()
        if not app:
            raise RuntimeError("Could not access Live application")
        if not hasattr(app, "browser") or app.browser is None:
            raise RuntimeError(
                "Browser is not available in the Live application"
            )
        browser_attrs = [
            attr
            for attr in dir(app.browser)
            if not attr.startswith("_")
        ]
        path_parts = path.split("/")
        if not path_parts:
            raise ValueError("Invalid path")
        root_category = path_parts[0].lower()
        current_item = None
        if root_category == "instruments" and hasattr(app.browser, "instruments"):
            current_item = app.browser.instruments
        elif root_category == "sounds" and hasattr(app.browser, "sounds"):
            current_item = app.browser.sounds
        elif root_category == "drums" and hasattr(app.browser, "drums"):
            current_item = app.browser.drums
        elif root_category == "audio_effects" and hasattr(
            app.browser, "audio_effects"
        ):
            current_item = app.browser.audio_effects
        elif root_category == "midi_effects" and hasattr(
            app.browser, "midi_effects"
        ):
            current_item = app.browser.midi_effects
        else:
            found = False
            for attr in browser_attrs:
                if attr.lower() == root_category:
                    try:
                        current_item = getattr(app.browser, attr)
                        found = True
                        break
                    except Exception as e:
                        if ctrl:
                            ctrl.log_message(
                                "Error accessing browser attribute {0}: {1}".format(
                                    attr, str(e)
                                )
                            )
            if not found:
                return {
                    "path": path,
                    "error": "Unknown or unavailable category: {0}".format(
                        root_category
                    ),
                    "available_categories": browser_attrs,
                    "items": [],
                }
        for i in range(1, len(path_parts)):
            part = path_parts[i]
            if not part:
                continue
            if not hasattr(current_item, "children"):
                return {
                    "path": path,
                    "error": "Item at '{0}' has no children".format(
                        "/".join(path_parts[:i])
                    ),
                    "items": [],
                }
            found = False
            for child in current_item.children:
                if hasattr(child, "name") and child.name.lower() == part.lower():
                    current_item = child
                    found = True
                    break
            if not found:
                return {
                    "path": path,
                    "error": "Path part '{0}' not found".format(part),
                    "items": [],
                }
        items = []
        if hasattr(current_item, "children"):
            for child in current_item.children:
                item_info = {
                    "name": child.name if hasattr(child, "name") else "Unknown",
                    "is_folder": hasattr(child, "children")
                    and bool(child.children),
                    "is_device": hasattr(child, "is_device") and child.is_device,
                    "is_loadable": hasattr(child, "is_loadable")
                    and child.is_loadable,
                    "uri": child.uri if hasattr(child, "uri") else None,
                }
                items.append(item_info)
        result = {
            "path": path,
            "name": current_item.name if hasattr(current_item, "name") else "Unknown",
            "uri": current_item.uri if hasattr(current_item, "uri") else None,
            "is_folder": hasattr(current_item, "children")
            and bool(current_item.children),
            "is_device": hasattr(current_item, "is_device")
            and current_item.is_device,
            "is_loadable": hasattr(current_item, "is_loadable")
            and current_item.is_loadable,
            "items": items,
        }
        if ctrl:
            ctrl.log_message(
                "Retrieved {0} items at path: {1}".format(len(items), path)
            )
        return result
    except Exception as e:
        if ctrl:
            ctrl.log_message(
                "Error getting browser items at path: {0}".format(str(e))
            )
            ctrl.log_message(traceback.format_exc())
        raise


def get_browser_categories(song, category_type, ctrl=None):
    """Get browser categories (alias for get_browser_tree)."""
    return get_browser_tree(song, category_type, ctrl)


def get_browser_items(song, path, item_type, ctrl=None):
    """Get browser items at path (alias for get_browser_items_at_path; item_type ignored)."""
    return get_browser_items_at_path(song, path, ctrl)
