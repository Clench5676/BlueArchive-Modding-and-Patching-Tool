"""Native requests and coordinate contracts, without creating a window or loading a GUI backend."""

import ctypes
import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from ba_modding_toolkit.errors import ServiceError
from ba_modding_toolkit.webui.events import EventBus
from ba_modding_toolkit.webui.jobs import JobRunner
from ba_modding_toolkit.webui.window_chrome import Rect, WindowChrome, union, vertical_offsets
from ba_modding_toolkit.webui.window_close import CloseCoordinator
from ba_modding_toolkit.webui.window_macos import CocoaHost
from ba_modding_toolkit.webui.window_windows import WindowsHost, aspect_rect, WM_SIZING, WM_DPICHANGED


class Host:
    def __init__(self):
        self.bounds = Rect(12, 16, 80, 30)
        self.current = dict(maximized=False, fullscreen=False, minimized=False)
        self.calls = []

    def install(self):
        self.calls.append('install')

    def dispatch(self, fn):
        self.calls.append('dispatch')
        fn()

    def measure(self, layout):
        self.calls.append(('measure', layout))
        if isinstance(self.bounds, Exception):
            raise self.bounds
        return self.bounds

    def state(self):
        return self.current.copy()

    def command(self, action):
        self.calls.append(action)

    def dispose(self):
        self.calls.append('dispose')


def test_vertical_translation_preserves_horizontal_frames_and_group_centre():
    rects = [Rect(12, 8, 26, 22), Rect(32, 9, 46, 21), Rect(52, 8, 66, 22)]
    for centre in [20.32, 24.635, 29.5619, 44.3428]:
        moved = vertical_offsets(rects, centre)
        bounds = union(moved)
        assert (bounds.top + bounds.bottom) / 2 == pytest.approx(centre)
        for old, new in zip(rects, moved):
            assert (new.left, new.right, new.bottom - new.top) == (old.left, old.right, old.bottom - old.top)
    with pytest.raises(ValueError):
        union([Rect(0, 0, float('nan'), 1)])


def test_adapter_keeps_last_valid_safe_area_and_fails_back_after_relayout_failure():
    host = Host()
    chrome = WindowChrome(NS(), EventBus(), platform='darwin', host=host)
    chrome.install()
    previous = chrome.bounds
    host.bounds = None
    chrome.refresh()
    assert chrome.active and not chrome.valid and chrome.bounds == previous
    host.bounds = RuntimeError('AppKit relayout rejected vertical alignment')
    chrome.refresh()
    assert not chrome.active and chrome.bounds is None
    assert 'relayout' in chrome.reason and host.calls[-1] == 'dispose'


def test_native_events_are_authoritative_for_zoom_fullscreen_and_commands():
    host = Host()
    chrome = WindowChrome(NS(), EventBus(), host=host)
    chrome.install()
    for current in [dict(maximized=True, fullscreen=False, minimized=False),
                    dict(maximized=False, fullscreen=True, minimized=False)]:
        host.current = current
        chrome.fallback_state('maximized', False)
        assert chrome.snapshot()['state'] == current
    chrome.command('maximize')
    assert host.calls[-2:] == ['dispatch', 'maximize']
    chrome.update(24.6, 53.3, [dict(left=90, top=0, right=150, bottom=50)])
    assert chrome.layout['drag'] == [Rect(90, 0, 150, 50)]
    for bad in [float('nan'), -1, 900, True]:
        with pytest.raises(ValueError):
            chrome.update(bad, 44, [])


def test_unsupported_host_is_deliberate_and_fallback_maximize_uses_events():
    calls = []
    window = NS(maximize=lambda: calls.append('maximize'), restore=lambda: calls.append('restore'))
    chrome = WindowChrome(window, EventBus(), platform='unsupported')
    chrome.install()
    assert not chrome.active and chrome.reason == 'unsupported-host'
    chrome.fallback_state('maximized', True)
    chrome.command('maximize')
    chrome.fallback_state('maximized', False)
    chrome.command('maximize')
    assert calls == ['restore', 'maximize'] and not hasattr(window, 'maximized')


def rect(x, y, w, h):
    return NS(origin=NS(x=x, y=y), size=NS(width=w, height=h))


class Button:
    def __init__(self, x, y, flipped, container_height=200):
        self.value, self.hidden = rect(x, y, 14, 14), False
        self.parent = NS(isFlipped=lambda: flipped, bounds=lambda: rect(0, 0, 200, container_height))

    def frame(self):
        return self.value

    def bounds(self):
        return rect(0, 0, 14, 14)

    def superview(self):
        return self.parent

    def convertRect_toView_(self, bounds, view):
        return self.value

    def setFrameOrigin_(self, point):
        self.value.origin = point if hasattr(point, "x") else NS(x=point[0], y=point[1])

    def isHidden(self):
        return self.hidden

    def setHidden_(self, value):
        self.hidden = value


@pytest.mark.parametrize('flipped', [True, False])
def test_cocoa_moves_only_y_and_leaves_fullscreen_reveal_to_appkit(flipped):
    host = CocoaHost.__new__(CocoaHost)
    host.api = NS(NSContainsRect=lambda outer, inner: outer.origin.y <= inner.origin.y and inner.origin.y + inner.size.height <= outer.origin.y + outer.size.height,
                  NSWindowStyleMaskFullScreen=1 << 14)
    state = {'fullscreen': False, 'mini': False}
    content = NS(isFlipped=lambda: flipped, bounds=lambda: rect(0, 0, 200, 200))
    host.window = NS(contentView=lambda: content, isMiniaturized=lambda: state['mini'], isZoomed=lambda: False,
                     styleMask=lambda: 1 << 14 if state['fullscreen'] else 0)
    host.transition, host.retries = False, 0
    host.buttons = [Button(x, 8 if flipped else 178, flipped) for x in [12, 32, 52]]
    bounds = host.measure({'centre': 29.5619})
    assert (bounds.top + bounds.bottom) / 2 == pytest.approx(29.5619)
    assert [b.frame().origin.x for b in host.buttons] == [12, 32, 52]
    state['fullscreen'] = True
    host.buttons[0].hidden = True
    before = [b.frame().origin.y for b in host.buttons]
    host.measure({'centre': 80})
    assert [b.frame().origin.y for b in host.buttons] == before
    host.transition = True
    assert host.measure({'centre': 30}) is None
    host.transition = False
    state.update(fullscreen=False, mini=True)
    assert host.measure({'centre': 30}) is None
    state['mini'] = False
    # a wide window: the bar's centre line is lower than a 32-high title bar container lets the buttons go. They go as low as
    # it allows and stay inside it; the native chrome is not given up (a second display, BL-073)
    # (the container spans the content's top 32 points; this stand-in converts frames unchanged, so an unflipped container
    # starts 168 up)
    base = 0 if flipped else 168
    for button in host.buttons:
        button.parent.bounds = lambda: rect(0, base, 200, 32)
        button.value.origin = NS(x=button.value.origin.x, y=3 if flipped else base + 15)
    bounds = host.measure({'centre': 40})
    assert bounds.bottom == pytest.approx(32) and bounds.top == pytest.approx(18)
    for button in host.buttons:
        assert base <= button.frame().origin.y <= base + 32 - 14
    # inside the room, the centre line is still met
    bounds = host.measure({'centre': 20})
    assert (bounds.top + bounds.bottom) / 2 == pytest.approx(20)
    # upwards the same: never above the container's top
    bounds = host.measure({'centre': 2})
    assert bounds.top == pytest.approx(0)


class Hook:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def __isub__(self, fn):
        self.handlers.remove(fn)
        return self


def test_windows_standard_caption_does_not_access_or_clip_webview_or_extend_frame(windows_host):
    # No WebView2, DWM or style setter is supplied. Installation precedes navigation.
    host = windows_host.host
    form = host.form
    chrome = WindowChrome(host.window, EventBus(), platform='win32', host=host)
    host.owner = chrome
    chrome.install()
    assert chrome.active and chrome.valid
    assert chrome.snapshot()['mode'] == 'standard' and chrome.bounds is None
    assert len(form.Resize.handlers) == len(form.DpiChanged.handlers) == 1
    form.WindowState = 'Maximized'
    form.Resize.handlers[0]()
    assert chrome.state['maximized']
    chrome.dispose()
    assert not form.Resize.handlers and not form.DpiChanged.handlers


def test_windows_standard_caption_survives_adapter_failure_without_html_fallback():
    host = Host()
    host.bounds = RuntimeError('native adapter failed')
    chrome = WindowChrome(NS(), EventBus(), platform='win32', host=host)
    chrome.install()
    assert not chrome.active and chrome.snapshot()['mode'] == 'standard'


@pytest.fixture
def closer():
    bus = EventBus()
    services = NS(jobs=JobRunner(bus), events=bus, classic=NS(starting=nullcontext))
    callbacks, calls = [], []
    def evaluate(script, callback=None):
        if callback and 'bamtValidateClose' in script:
            callback(True)
        elif callback:
            callbacks.append(callback)
            requested.set()
        else:
            calls.append('thaw')
            thawed.set()
    requested, thawed = threading.Event(), threading.Event()
    services.window = NS(evaluate_js=evaluate)
    coordinator = CloseCoordinator(services, lambda: (calls.append('close'), coordinator.closed()), prompt_wait=.1, close_wait=.01)
    yield NS(s=services, api=coordinator, callbacks=callbacks, calls=calls, requested=requested, thawed=thawed)
    services.jobs.shutdown(wait=1)


def test_native_requests_coalesce_and_reserve_only_after_draft_preflight(closer):
    c = closer
    assert c.api.native_closing() is False
    future = c.api.request()
    assert c.requested.wait(1)
    assert c.api.request() is future
    # Save and Leave may still submit its library writer while the prompt is open.
    writer = c.s.jobs.submit('save', lambda log, progress: None, writes='library')
    assert c.s.jobs.wait(writer, 1)
    c.callbacks[0](True)
    assert future.result(1) is True
    assert c.calls == ['close'] and c.api.native_closing() is True
    with pytest.raises(ServiceError, match='busy'):
        c.s.jobs.submit('late', lambda log, progress: None, writes='game')


@pytest.mark.parametrize('answer', [False, {'message': 'save failed'}, None])
def test_stay_save_failure_and_unresponsive_page_cancel_and_release(closer, answer):
    c = closer
    future = c.api.request()
    assert c.requested.wait(1)
    if answer is not None: c.callbacks[0](answer)
    assert future.result(1) is False
    assert c.thawed.wait(1)
    assert c.calls == ['thaw'] and not c.api.permitted
    c.s.jobs.reserve_quit(); c.s.jobs.cancel_quit()
    # An old permission arriving after timeout cannot destroy the window.
    c.callbacks[0](True)
    assert 'close' not in c.calls


def test_writer_arriving_during_prompt_refuses_final_close(closer):
    c = closer
    future = c.api.request()
    assert c.requested.wait(1)
    gate = threading.Event()
    writer = c.s.jobs.submit('writer', lambda log, progress: gate.wait(1), writes='game')
    try:
        c.callbacks[0](True)
        with pytest.raises(ServiceError, match='busy'): future.result(1)
        assert 'close' not in c.calls
    finally:
        gate.set(); assert c.s.jobs.wait(writer, 1)
    c.s.jobs.reserve_quit(); c.s.jobs.cancel_quit()


@pytest.mark.parametrize('refusal', ['silent', 'exception'])
def test_native_refusal_revokes_permit_and_owned_reservation(closer, refusal):
    c = closer
    def close():
        if refusal == 'exception': raise RuntimeError('host refused')
    future = c.api.request(close)
    assert c.requested.wait(1)
    c.callbacks[0](True)
    if refusal == 'exception':
        with pytest.raises(RuntimeError): future.result(1)
    else: assert future.result(1) is False
    assert not c.api.permitted
    c.s.jobs.reserve_quit(); c.s.jobs.cancel_quit()


def test_foreign_update_reservation_is_never_released_by_close_coordinator(closer):
    c = closer
    future = c.api.request(lambda: None, reserve=False)
    assert c.requested.wait(1)
    c.s.jobs.reserve_quit()
    c.callbacks[0](True)
    assert future.result(1) is False
    with pytest.raises(ServiceError, match='busy'): c.s.jobs.reserve_quit()
    c.s.jobs.cancel_quit()


def test_startup_without_a_loaded_editor_closes_without_waiting_for_assets(closer):
    c = closer
    c.api.page_loaded = False
    assert c.api.request().result(1) is True
    assert not c.requested.is_set() and c.calls == ['close']


def test_new_draft_revision_invalidates_permission_and_releases_reservation(closer):
    c = closer
    original = c.s.window.evaluate_js
    def evaluate(script, callback=None):
        if callback and 'bamtValidateClose' in script:
            callback(False)
        else: original(script, callback)
    c.s.window.evaluate_js = evaluate
    future = c.api.request()
    assert c.requested.wait(1)
    c.callbacks[0](True)
    assert future.result(1) is False
    assert not c.api.permitted and 'close' not in c.calls
    c.s.jobs.reserve_quit(); c.s.jobs.cancel_quit()


def test_windows_subclass_retains_callback_forwards_and_cleans_up():
    from ba_modding_toolkit.webui.window_windows import WindowSubclass
    class Function:
        def __init__(self, fn): self.fn = fn
        def __call__(self, *args): return self.fn(*args)
    calls = []
    api = NS(SetWindowSubclass=Function(lambda *args: calls.append(('install', args)) or True),
             RemoveWindowSubclass=Function(lambda *args: calls.append(('remove', args))),
             DefSubclassProc=Function(lambda *args: calls.append(('default', args)) or 42))
    errors = []
    def message(code, wparam, lparam, default):
        if code == 7: raise RuntimeError('message failed')
        if code == 8:
            default()
            raise RuntimeError('post-default policy failed')
        return default()
    hook = WindowSubclass(123, message, errors.append, api=api, callback_type=lambda *signature: lambda fn: fn)
    assert hook.callback(123, 1, 2, 3, hook.identifier, 0) == 42
    assert hook.callback(123, 7, 2, 3, hook.identifier, 0) == 42
    assert hook.callback(123, 8, 2, 3, hook.identifier, 0) == 42
    hook.dispose()
    assert [item[0] for item in calls] == ['install','default','default','default','remove']
    assert len(errors) == 2
    assert calls[0][1][1] is calls[-1][1][1] is hook.callback


def test_macos_keyboard_and_edit_menu_restore_after_rebuild_without_timer(monkeypatch):
    from ba_modding_toolkit.webui.native import install_window_commands
    class Object:
        @classmethod
        def alloc(cls): return cls()
        def init(self): return self
    class Item(Object):
        def __init__(self): self.menu = None
        def setSubmenu_(self, menu): self.menu = menu
        def setTitle_(self, title): self.title = title
        def submenu(self): return self.menu
        def action(self): return self.selector
        def setAction_(self, selector): self.selector = selector
        def setTarget_(self, target): self.target = target
    class Menu(Object):
        def __init__(self): self.items = []
        def initWithTitle_(self, title): self.title = title; return self
        def itemArray(self): return self.items
        def addItem_(self, item): self.items.append(item)
        def addItemWithTitle_action_keyEquivalent_(self, title, selector, key):
            item = Item(); item.title, item.selector, item.key = title, selector, key
            self.items.append(item); return item
    def app_menu():
        main, app = Menu(), Menu()
        item = Item(); item.setSubmenu_(app); main.addItem_(item)
        quit_item = app.addItemWithTitle_action_keyEquivalent_('Quit', 'terminate:', 'q')
        return main, quit_item
    monitors, removed, calls, observers, deferred = [], [], [], [], []
    current = {"main": None}
    def observe(name, obj, queue, fn):
        assert name == 'became-key' and obj is window and queue is None
        observers.append(fn)
        return fn
    centre = NS(addObserverForName_object_queue_usingBlock_=observe, removeObserver_=removed.append)
    # No NSTimer is provided: using one fails this test immediately.
    api = NS(NSObject=Object, NSMenu=Menu, NSMenuItem=Item,
             NSApplication=NS(sharedApplication=lambda: NS(mainMenu=lambda: current["main"])),
             NSNotificationCenter=NS(defaultCenter=lambda: centre), NSWindowDidBecomeKeyNotification='became-key',
             NSEvent=NS(addLocalMonitorForEventsMatchingMask_handler_=lambda mask, fn: monitors.append(fn) or fn,
                        removeMonitor_=removed.append), NSEventMaskKeyDown=1,
             NSEventModifierFlagCommand=1, NSEventModifierFlagShift=2,
             NSEventModifierFlagOption=4, NSEventModifierFlagControl=8)
    monkeypatch.setitem(sys.modules, 'AppKit', api)
    monkeypatch.setitem(sys.modules, 'PyObjCTools', NS(AppHelper=NS(callAfter=deferred.append)))
    window = NS(performMiniaturize_=lambda sender: calls.append('minimize'),
                toggleFullScreen_=lambda sender: calls.append('fullscreen'))
    services = NS(window=NS(native=window), quit=lambda: calls.append('guarded-close'))
    cleanup = install_window_commands(services, None)
    assert len(deferred) == 1, 'first_show repair runs after pywebview creates its menu'
    main, quit_item = app_menu(); current["main"] = main
    deferred.pop(0)(); cleanup.refresh()
    def assert_menu(main, quit_item):
        assert len(main.items) == 2, 'only App and Edit; no Window menu or duplicates'
        assert [item.selector for item in main.items[1].submenu().items] == ['cut:', 'copy:', 'paste:', 'selectAll:']
        assert [item.key for item in main.items[1].submenu().items] == ['x', 'c', 'v', 'a']
        assert quit_item.selector == 'closeWindow:'
    assert_menu(main, quit_item)
    target = quit_item.target
    target.closeWindow_(None)
    for char, flags in [('q',1),('w',1),('m',1),('f',9)]:
        event = NS(window=lambda: window, modifierFlags=lambda: flags, charactersIgnoringModifiers=lambda: char)
        assert monitors[0](event) is None
    for char, flags, event_window in [('c',1,window),('q',3,window),('w',5,window),('q',9,window),('m',9,window),('q',1,object())]:
        event = NS(window=lambda: event_window, modifierFlags=lambda: flags, charactersIgnoringModifiers=lambda: char)
        assert monitors[0](event) is event
    assert calls == ['guarded-close','guarded-close','guarded-close','minimize','fullscreen']
    def busy():
        raise ServiceError('busy')
    services.quit = busy
    services.events = NS(publish=lambda event: calls.append(event))
    event = NS(window=lambda: window, modifierFlags=lambda: 1, charactersIgnoringModifiers=lambda: 'q')
    assert monitors[0](event) is None
    assert calls[-1] == 'app.busy'
    # Observer delivery may precede the pywebview delegate's menu rebuild.
    # Neither replacing the whole menu nor clearing it in place may lose Edit/Quit.
    for replace in [True, False]:
        observers[0](None)
        rebuilt, quit_item = app_menu()
        if replace:
            current["main"] = rebuilt
        else:
            current["main"].items[:] = rebuilt.items
        assert quit_item.selector == 'terminate:', 'repair must be deferred beyond the delegate'
        deferred.pop(0)()
        assert_menu(current["main"], quit_item)
        assert quit_item.target is target
        observers[0](None); deferred.pop(0)()
        assert_menu(current["main"], quit_item)
    observers[0](None)
    cleanup()
    assert removed == observers + monitors
    current["main"], quit_item = app_menu()
    deferred.pop(0)()
    assert quit_item.selector == 'terminate:', 'queued callbacks become inert after disposal'


def test_cocoa_window_closing_without_content_view_keeps_the_native_chrome():
    # pywebview's windowWillClose removes the web view, the window's content view, before AppKit posts the window's
    # last notifications (resign key). The refresh they trigger measured a None view and gave the chrome up (BL-078).
    host = CocoaHost.__new__(CocoaHost)
    host.api = NS(NSWindowStyleMaskFullScreen=1 << 14)
    host.window = NS(contentView=lambda: None, isMiniaturized=lambda: False, isZoomed=lambda: False, styleMask=lambda: 0)
    host.transition, host.retries, retries = False, 0, []
    host.later = lambda delay, fn: retries.append((delay, fn))
    host.owner = NS(refresh=lambda: None)
    host.buttons = [Button(x, 8, True) for x in [12, 32, 52]]
    host.dispatch = lambda fn: fn()
    host.dispose = lambda: pytest.fail("the native chrome was given up")
    before = [b.frame().origin.y for b in host.buttons]
    assert host.measure({'centre': 30}) is None
    assert [b.frame().origin.y for b in host.buttons] == before
    chrome = WindowChrome(NS(), EventBus(), platform='darwin', host=host)
    chrome.active, chrome.valid = True, True
    chrome.refresh()
    assert chrome.active and not chrome.valid and chrome.reason == "not-installed"


def test_cocoa_measures_again_until_a_content_view_arrives(caplog):
    # No AppKit notification follows when pywebview puts its web view in place: without another measurement the
    # chrome stayed waiting, with no drag regions, until the window was moved or resized (BL-078).
    host = CocoaHost.__new__(CocoaHost)
    host.api = NS(NSContainsRect=lambda outer, inner: True, NSWindowStyleMaskFullScreen=1 << 14)
    content, scheduled = {'view': None}, []
    host.window = NS(contentView=lambda: content['view'], isMiniaturized=lambda: False, isZoomed=lambda: False,
                     styleMask=lambda: 0)
    host.transition, host.retries = False, 0
    host.buttons = [Button(x, 8, True) for x in [12, 32, 52]]
    host.later = lambda delay, fn: scheduled.append((delay, fn))
    host.install = lambda: None
    host.dispose = lambda: pytest.fail("the native chrome was given up")
    chrome = WindowChrome(NS(), EventBus(), platform='darwin', host=host)
    host.owner = chrome
    with caplog.at_level("INFO", logger="ba_modding_toolkit.webui.window_chrome"):
        chrome.install()
        assert chrome.active and not chrome.valid
        assert [delay for delay, _ in scheduled] == [CocoaHost.RETRY_DELAY]
        content['view'] = NS(isFlipped=lambda: True, bounds=lambda: rect(0, 0, 200, 200))
        scheduled.pop()[1]()
    assert chrome.valid and host.retries == 0 and not scheduled
    assert "Native window chrome active: darwin" in caplog.text and "Native window chrome valid" in caplog.text
    # a content view that never comes back (the window closed) is not measured forever
    content['view'] = None
    for _ in range(CocoaHost.RETRY_LIMIT + 3):
        chrome.refresh()
    assert len(scheduled) == CocoaHost.RETRY_LIMIT


@pytest.fixture
def cocoa_drag():
    calls, keys = [], []
    host = CocoaHost.__new__(CocoaHost)
    host.owner = NS(active=True, valid=True, layout={"height": 44, "drag": [Rect(90, 0, 150, 44)]})
    host.transition, host.ignored = False, ""
    state = {"flipped": False, "action": None}
    # A nonzero bounds origin and a changed content view exercise the conversion,
    # rather than assuming locationInWindow already uses web coordinates.
    def content():
        return NS(bounds=lambda: rect(10, 20, 960, 540), isFlipped=lambda: state["flipped"],
                  convertPoint_fromView_=lambda point, source: NS(x=point.x + 10, y=point.y + 20))
    host.window = NS(contentView=content, performWindowDragWithEvent_=lambda event: calls.append("drag"),
                     performMiniaturize_=lambda sender: calls.append("minimize"),
                     performZoom_=lambda sender: calls.append("zoom"))
    def preference(key):
        keys.append(key)
        return state["action"]
    host.api = NS(NSUserDefaults=NS(standardUserDefaults=lambda: NS(stringForKey_=preference)))
    def event(x=100, y=10, count=1, window=None):
        return NS(window=lambda: window or host.window, clickCount=lambda: count,
                  locationInWindow=lambda: NS(x=x, y=y if state["flipped"] else 540-y))
    return NS(host=host, event=event, calls=calls, state=state, keys=keys)


@pytest.mark.parametrize("flipped", [True, False])
@pytest.mark.parametrize("point,hit", [((90, 0), True), ((149.9, 43.9), True), ((150, 10), False),
                                       ((100, 44), False), ((89, 10), False), ((100, -1), False)])
def test_cocoa_drag_hit_test_converts_to_content_top_left(cocoa_drag, flipped, point, hit):
    f = cocoa_drag; f.state["flipped"] = flipped
    event = f.event(*point)
    assert f.host.mouse_down(event) is (None if hit else event)
    assert f.calls == (["drag"] if hit else [])
    assert not f.keys, "a single press does not read preferences"


@pytest.mark.parametrize("action,expected", [("Minimize", "minimize"), ("None", None), ("Do Nothing", None),
                                            ("Maximize", "zoom"), ("Zoom", "zoom"), (None, "zoom"), ("unknown", "zoom")])
def test_cocoa_double_click_respects_system_title_bar_action(cocoa_drag, action, expected):
    f = cocoa_drag; f.state["action"] = action
    assert f.host.mouse_down(f.event(count=2)) is None
    assert f.calls == ([expected] if expected else [])
    assert f.keys == ["AppleActionOnDoubleClick"]


@pytest.mark.parametrize("blocked", ["popup", "invalid", "inactive", "transition", "other-window", "no-content"])
def test_cocoa_drag_passes_unhandled_events_through(cocoa_drag, blocked):
    f = cocoa_drag
    if blocked == "no-content": f.host.window.contentView = lambda: None
    if blocked == "popup": f.host.owner.layout["drag"] = []
    if blocked == "invalid": f.host.owner.valid = False
    if blocked == "inactive": f.host.owner.active = False
    if blocked == "transition": f.host.transition = True
    event = f.event(window=object() if blocked == "other-window" else None)
    assert f.host.mouse_down(event) is event
    assert not f.calls and not f.keys


def test_cocoa_logs_why_a_top_bar_press_cannot_drag_once_per_reason(cocoa_drag, caplog):
    f = cocoa_drag
    with caplog.at_level("INFO", logger="ba_modding_toolkit.webui.window_macos"):
        f.host.owner.valid = False
        for _ in range(3):
            f.host.mouse_down(f.event())
        f.host.owner.valid = True
        f.host.owner.layout["drag"] = []
        f.host.mouse_down(f.event())
        f.host.mouse_down(f.event(y=300))  # below the bar: not a top bar press
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["Native window chrome ignored a top bar press: invalid",
                     "Native window chrome ignored a top bar press: no drag regions"]
    assert not f.calls


def test_rejected_toolbar_layout_is_logged_once_and_keeps_the_previous_regions(caplog):
    chrome = WindowChrome(NS(), EventBus(), platform='darwin', host=Host())
    good = [{"left": 90, "top": 0, "right": 150, "bottom": 44}]
    with caplog.at_level("INFO", logger="ba_modding_toolkit.webui.window_chrome"):
        chrome.update(22, 44, good)
        for _ in range(2):
            with pytest.raises(ValueError):
                chrome.update(22, 44, good * 65)
        chrome.update(22, 44, [])
    assert chrome.layout["drag"] == []
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["Native window chrome drag regions: 1",
                     "Native window chrome rejected the toolbar layout: Invalid toolbar drag regions (65 drag regions)",
                     "Native window chrome drag regions: 0 (covered)"]


def test_empty_drag_regions_are_logged_with_the_page_state(caplog):
    # BL-078: the log names why the top bar has no drag regions, so a report shows the screen's state.
    chrome = WindowChrome(NS(), EventBus(), platform='darwin', host=Host())
    good = [{"left": 90, "top": 0, "right": 150, "bottom": 44}]
    with caplog.at_level("INFO", logger="ba_modding_toolkit.webui.window_chrome"):
        chrome.update(22, 44, [], "waiting")
        chrome.update(22, 44, good)
        chrome.update(22, 44, [], "immersive")
        chrome.update(22, 44, [], "immersive")
        chrome.update(22, 44, [], "waiting")
        chrome.update(22, 44, good, "")
        with pytest.raises(ValueError):
            chrome.update(22, 44, [], "popup")
    assert chrome.layout["drag"] == [Rect(**good[0])]
    assert [r.getMessage() for r in caplog.records] == [
        "Native window chrome drag regions: 0 (waiting)",
        "Native window chrome drag regions: 1",
        "Native window chrome drag regions: 0 (immersive)",
        "Native window chrome drag regions: 0 (waiting)",
        "Native window chrome drag regions: 1",
        "Native window chrome rejected the toolbar layout: Invalid toolbar state (0 drag regions)"]


def test_cocoa_installs_and_removes_a_window_scoped_mouse_monitor(cocoa_drag):
    f = cocoa_drag
    buttons = [Button(x, 8, True) for x in (12, 32, 52)]
    for button in buttons:
        button.hidden = True
        button.setHidden_ = lambda hidden, button=button: setattr(button, 'hidden', hidden)
    aspect = []
    f.host.window.setContentAspectRatio_ = aspect.append
    f.host.window.standardWindowButton_ = lambda kind: buttons[kind]
    monitors, removed, observers = [], [], []
    centre = NS(addObserverForName_object_queue_usingBlock_=lambda *args: observers.append(args) or args,
                removeObserver_=removed.append)
    api = NS(NSWindowCloseButton=0, NSWindowMiniaturizeButton=1, NSWindowZoomButton=2,
             NSUserDefaults=f.host.api.NSUserDefaults,
             NSEventMaskLeftMouseDown=2, NSEventMaskMouseMoved=32,
             NSEvent=NS(addLocalMonitorForEventsMatchingMask_handler_=lambda mask, fn: monitors.append((mask, fn)) or fn,
                        removeMonitor_=removed.append), NSNotificationCenter=NS(defaultCenter=lambda: centre))
    names = ('DidResize', 'DidMove', 'DidBecomeKey', 'DidResignKey', 'DidMiniaturize', 'DidDeminiaturize',
             'WillEnterFullScreen', 'WillExitFullScreen', 'DidEnterFullScreen', 'DidExitFullScreen')
    for name in names:
        setattr(api, 'NSWindow' + name + 'Notification', name)
    host = CocoaHost(NS(native=f.host.window), f.host.owner, api=api, dispatch=lambda fn: fn(), later=lambda delay, fn: None)
    host.install()
    assert aspect == [(16, 9)]
    assert monitors[0][0] == api.NSEventMaskLeftMouseDown
    assert monitors[0][1](f.event()) is None
    assert f.calls == ['drag'] and not any(button.hidden for button in buttons)
    host.dispose()
    assert all(button.hidden for button in buttons)
    assert monitors[1][0] == api.NSEventMaskMouseMoved, "the pointer's log watches moves"
    assert removed == observers + [monitors[0][1], monitors[1][1]]
    assert host.monitor is None and not hasattr(host, 'overlay')


class NativeFunction:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


@pytest.fixture
def windows_host(monkeypatch):
    # Screen coordinates deliberately include a negative monitor origin.
    current = [-1800, -100, -184, 839]
    calls, hooks = [], []
    state = {'dpi': 96}
    form = NS(Resize=Hook(), DpiChanged=Hook(), HandleCreated=Hook(), WindowState='Normal',
              is_fullscreen=False, Handle=NS(ToInt64=lambda: 123), DeviceDpi=96,
              MinimumSize=(960, 540), IsDisposed=False)
    monkeypatch.setitem(sys.modules, 'System.Drawing', NS(Size=lambda w, h: (w, h)))
    def frame(output, style, menu, extended, dpi):
        calls.append(('frame', style, menu, extended, dpi))
        fw, fh = round(16 * dpi / 96), round(39 * dpi / 96)
        output._obj.left, output._obj.top = -fw // 2, -(fh - fw // 2)
        output._obj.right, output._obj.bottom = fw // 2, fw // 2
        return True
    def get_rect(handle, output):
        output._obj.left, output._obj.top, output._obj.right, output._obj.bottom = current
        return True
    def set_rect(handle, other, x, y, w, h, flags):
        calls.append(('size', x, y, w, h, flags))
        current[:] = [x, y, x + w, y + h]
        return True
    api = NS(GetWindowLongPtrW=NativeFunction(lambda handle, index: 0xCF0000 if index == -16 else 0x100),
             GetMenu=NativeFunction(lambda handle: 0), GetWindowRect=NativeFunction(get_rect),
             AdjustWindowRectExForDpi=NativeFunction(frame), SetWindowPos=NativeFunction(set_rect),
             GetDpiForWindow=NativeFunction(lambda handle: state['dpi']))
    def subclass(handle, message, failed):
        hook = NS(handle=handle, message=message, disposed=False)
        hook.dispose = lambda: setattr(hook, 'disposed', True)
        hooks.append(hook)
        return hook
    window = NS(native=form, min_size=(960, 540), initial_width=1600, initial_height=900)
    host = WindowsHost(window, NS(refresh=lambda: calls.append('refresh')), api=api, subclass_factory=subclass)
    return NS(host=host, calls=calls, hooks=hooks, current=current, state=state)


@pytest.mark.parametrize('dpi', [96, 120, 144, 192, 288])
@pytest.mark.parametrize('edge', range(1, 9))
@pytest.mark.parametrize('size', [(1317, 827), (300, 200), (2573, 1001)])
def test_windows_aspect_correction_for_every_edge_minimum_and_dpi(dpi, edge, size):
    import math
    fw, fh = round(16 * dpi / 96), round(39 * dpi / 96)
    original = Rect(-1920, -200, -1920 + size[0] + fw, -200 + size[1] + fh)
    corrected = aspect_rect(original, edge, (fw, fh), dpi=dpi)
    width, height = corrected.right - corrected.left - fw, corrected.bottom - corrected.top - fh
    assert width >= math.ceil(960 * dpi / 96) and height >= math.ceil(540 * dpi / 96)
    if edge in (1, 2):
        assert abs(height - width * 9 / 16) <= .5
    else:
        assert abs(width - height * 16 / 9) <= .5
    assert (corrected.right if edge in (1, 4, 7) else corrected.left) == (original.right if edge in (1, 4, 7) else original.left)
    assert (corrected.bottom if edge in (3, 4, 5) else corrected.top) == (original.bottom if edge in (3, 4, 5) else original.top)
    assert all(isinstance(value, int) for value in (corrected.left, corrected.top, corrected.right, corrected.bottom))


@pytest.mark.parametrize('dpi', [96, 120, 144, 192])
@pytest.mark.parametrize('edge', range(1, 9))
def test_windows_sizing_message_corrects_the_physical_client_rect(windows_host, dpi, edge):
    from ctypes import wintypes
    f = windows_host
    f.state['dpi'] = dpi
    f.host.install()
    value = wintypes.RECT(-1300, -200, 400, 800)
    expected = aspect_rect(Rect(-1300, -200, 400, 800), edge, f.host.frame_size(), dpi=dpi)
    assert f.host.message(WM_SIZING, edge, ctypes.addressof(value), lambda: pytest.fail('WM_SIZING should be handled')) == 1
    assert (value.left, value.top, value.right, value.bottom) == (expected.left, expected.top, expected.right, expected.bottom)


@pytest.mark.parametrize('dpi', [96, 120, 144, 192])
def test_windows_startup_uses_client_dimensions_and_dpi_adjusted_frame(windows_host, dpi):
    f = windows_host
    f.state['dpi'] = dpi
    f.host.install()
    fw, fh = f.host.frame_size()
    assert f.current[2] - f.current[0] - fw == round(1600 * dpi / 96)
    assert f.current[3] - f.current[1] - fh == round(900 * dpi / 96)
    assert f.host.form.MinimumSize == (round(960 * dpi / 96) + fw, round(540 * dpi / 96) + fh)
    assert f.current[:2] == [-1800, -100]
    assert ('frame', 0xCF0000, False, 0x100, dpi) in f.calls
    f.host.dispose()
    assert f.hooks[0].disposed and f.host.form.MinimumSize == (960, 540)


@pytest.mark.parametrize('mode', ['Maximized', 'Minimized', 'Fullscreen'])
def test_windows_maximize_and_fullscreen_retain_os_geometry_and_default_messages(windows_host, mode):
    f = windows_host
    f.host.form.WindowState = mode if mode != 'Fullscreen' else 'Normal'
    f.host.form.is_fullscreen = mode == 'Fullscreen'
    f.host.install()
    assert not any(call[0] == 'size' for call in f.calls)
    assert f.host.message(WM_SIZING, 2, 0, lambda: 42) == 42
    assert f.host.message(0x83, 1, 0, lambda: 43) == 43
    assert f.host.message(0x84, 0, 0, lambda: 9) == 9
    assert f.host.message(0x2A2, 0, 0, lambda: 0) == 0


def test_windows_dpi_changes_run_default_first_and_rehook_recreated_handles(windows_host):
    f = windows_host
    f.host.install()
    f.calls.clear()
    def default():
        f.calls.append('default')
        f.state['dpi'] = 144
        return 42
    assert f.host.message(WM_DPICHANGED, 0, 0, default) == 42
    assert f.calls[0] == 'default'
    fw, fh = f.host.frame_size()
    assert f.host.form.MinimumSize == (1440 + fw, 810 + fh)
    f.host.form.Handle = NS(ToInt64=lambda: 456)
    f.host.form.HandleCreated.handlers[0]()
    assert f.hooks[0].disposed and f.hooks[1].handle == 456
    f.host.dispose()
    assert f.hooks[1].disposed and not f.host.form.HandleCreated.handlers


def test_cocoa_content_aspect_policy_survives_m1_alignment_failure():
    calls = []
    window = NS(setContentAspectRatio_=calls.append, standardWindowButton_=lambda kind: None)
    api = NS(NSWindowCloseButton=0, NSWindowMiniaturizeButton=1, NSWindowZoomButton=2)
    host = CocoaHost(NS(native=window), NS(), api=api, dispatch=lambda fn: fn(), later=lambda delay, fn: None)
    with pytest.raises(RuntimeError, match='button missing'):
        host.install()
    assert calls == [(16, 9)]


def test_windows_failed_aspect_install_keeps_standard_caption_and_cleans_subclass(windows_host):
    f = windows_host
    f.host.user.AdjustWindowRectExForDpi = NativeFunction(lambda *args: False)
    chrome = WindowChrome(f.host.window, EventBus(), platform='win32', host=f.host)
    f.host.owner = chrome
    chrome.install()
    assert not chrome.active and chrome.snapshot()['mode'] == 'standard'
    assert f.hooks[0].disposed
    assert not f.host.form.Resize.handlers and not f.host.form.HandleCreated.handlers
    assert f.host.form.MinimumSize == (960, 540)
    assert 'frame' in chrome.reason


def test_windows_installs_aspect_policy_before_show_without_waiting_for_webview(monkeypatch, tmp_path):
    from ba_modding_toolkit.webui import main, native, window_chrome
    from ba_modding_toolkit.webui.timing import Timings
    from ba_modding_toolkit.webui.tray import Tray
    calls = []
    handlers = {}
    class Event(Hook):
        def __init__(self, name):
            super().__init__()
            handlers[name] = self.handlers
    class Chrome:
        active = False
        def __init__(self, *args, **kwargs): pass
        def install(self): calls.append('install')
        def close(self): pass
        def dispose(self): calls.append('dispose')
    def create(title, url, **kwargs):
        assert kwargs['frameless'] is False
        return NS(events=NS(**{name: Event(name) for name in ('initialized', 'loaded', 'closing', 'closed',
                                                              'before_show', 'shown', 'maximized', 'minimized', 'restored')}))
    def start(**kwargs):
        calls.append('before-show')
        handlers['before_show'][0]()
        assert calls == ['before-show', 'install']
        assert not services.close_coordinator.page_loaded
        calls.append('shown')
        handlers['shown'][0]()
    monkeypatch.setattr(main.sys, 'platform', 'win32')
    monkeypatch.setattr(native, 'webview2_version', lambda: '120.0.0.0')
    monkeypatch.setattr(Tray, 'supported', staticmethod(lambda: False))
    monkeypatch.setattr(window_chrome, 'WindowChrome', Chrome)
    monkeypatch.setitem(sys.modules, 'webview', NS(settings={}, create_window=create, start=start))
    services = NS(window=None, data_dir=tmp_path, timings=Timings(), events=EventBus())
    main._run_window(services, NS(url='http://127.0.0.1:1/'), False)
    assert calls == ['before-show', 'install', 'shown', 'dispose']


def test_window_button_motion_is_checked_and_goes_to_hosts_that_play_it():
    # chrome-motion.js sends the top bar's offsets; the macOS buttons follow them, other hosts have none in the toolbar
    host = Host()
    played = []
    host.slide = lambda frames, period: played.append((frames, period))
    chrome = WindowChrome(NS(), EventBus(), platform='darwin', host=host)
    chrome.motion([1, 2], 1 / 60)
    assert played == [], "nothing before the chrome is active"
    chrome.install()
    chrome.motion([108.2, 50, 0], 1 / 60)
    assert played == [([108.2, 50.0, 0.0], 1 / 60)]
    for frames, period in [([], 1 / 60), ([1] * 121, 1 / 60), ([-1], 1 / 60), ([float('nan')], 1 / 60), ([True], 1 / 60),
                           ([4097], 1 / 60), ("12", 1 / 60), ([1], 0), ([1], 0.5), ([1], None)]:
        with pytest.raises(ValueError):
            chrome.motion(frames, period)
    assert len(played) == 1
    plain = WindowChrome(NS(), EventBus(), platform='win32', host=Host())
    plain.install()
    plain.motion([10], 1 / 60)


def cocoa_motion(flipped, fullscreen=None):
    host = CocoaHost.__new__(CocoaHost)
    host.api = NS(NSContainsRect=lambda outer, inner: outer.origin.y <= inner.origin.y and inner.origin.y + inner.size.height <= outer.origin.y + outer.size.height,
                  NSWindowStyleMaskFullScreen=1 << 14)
    fullscreen = fullscreen if fullscreen is not None else {'on': False}
    content = NS(isFlipped=lambda: flipped, bounds=lambda: rect(0, 0, 200, 200))
    host.window = NS(contentView=lambda: content, isMiniaturized=lambda: False, isZoomed=lambda: False,
                     styleMask=lambda: 1 << 14 if fullscreen['on'] else 0)
    host.transition, host.retries = False, 0
    host.buttons = [Button(x, 8 if flipped else 178, flipped) for x in [12, 32, 52]]
    pending = []
    host.later = lambda delay, fn: pending.append((delay, fn))
    return host, pending, fullscreen


def tops(host):
    # top edge in content points from the window's top, as the page measures
    flipped = host.buttons[0].parent.isFlipped()
    return [b.frame().origin.y if flipped else 200 - b.frame().origin.y - 14 for b in host.buttons]


@pytest.mark.parametrize('flipped', [True, False])
def test_cocoa_buttons_play_the_top_bar_motion_and_hide_once_above_the_window(flipped):
    host, pending, _ = cocoa_motion(flipped)
    resting = host.measure({'centre': 22})
    assert tops(host) == pytest.approx([15] * 3)
    host.slide([30.0, 10.0, 0.0], 1 / 60)
    assert tops(host) == pytest.approx([-15] * 3) and all(b.hidden for b in host.buttons), "wholly above the window"
    delay, step = pending.pop(0)
    assert delay == 1 / 60
    step()
    assert tops(host) == pytest.approx([5] * 3) and not any(b.hidden for b in host.buttons)
    # a newer motion replaces the one still playing
    host.slide([20.0], 1 / 60)
    pending.pop(0)[1]()
    assert tops(host) == pytest.approx([-5] * 3) and host.offset == 20
    # a layout pass aligns them again and keeps the offset
    bounds = host.measure({'centre': 25})
    assert bounds.top == pytest.approx(resting.top + 3) and tops(host) == pytest.approx([-2] * 3)
    assert [b.frame().origin.x for b in host.buttons] == [12, 32, 52]
    host.slide([0.0], 1 / 60)
    assert tops(host) == pytest.approx([18] * 3)


@pytest.mark.parametrize('flipped', [True, False])
def test_cocoa_fullscreen_gets_back_only_what_the_motion_changed(flipped):
    host, pending, fullscreen = cocoa_motion(flipped)
    host.measure({'centre': 22})
    host.slide([40.0], 1 / 60)
    assert all(b.hidden for b in host.buttons)
    # macOS fullscreen: AppKit laid the first button out itself; the others are still where the motion put them
    fullscreen['on'] = True
    appkit = NS(x=12, y=4 if flipped else 182)
    host.buttons[0].value.origin = appkit
    host.measure({'centre': 22})
    assert host.buttons[0].frame().origin is appkit, "AppKit's own layout stays"
    assert tops(host)[1:] == pytest.approx([15, 15]) and not any(b.hidden for b in host.buttons)
    host.slide([40.0], 1 / 60)
    assert tops(host)[1:] == pytest.approx([15, 15]) and not any(b.hidden for b in host.buttons), "nothing moves there"
    # leaving fullscreen AppKit lays the row out as one again; alignment and the offset return
    host.buttons[0].value.origin = NS(x=12, y=host.buttons[1].frame().origin.y)
    fullscreen['on'] = False
    host.measure({'centre': 22})
    assert tops(host) == pytest.approx([-25] * 3) and all(b.hidden for b in host.buttons)


def test_cocoa_dispose_stops_a_playing_motion():
    host, pending, _ = cocoa_motion(True)
    host.api.NSNotificationCenter = NS(defaultCenter=lambda: NS(removeObserver_=lambda observer: None))
    host.observers, host.monitor = [], None
    host.measure({'centre': 22})
    host.original = [(b.frame().origin, False) for b in host.buttons]
    host.slide([30.0, 0.0], 1 / 60)
    host.dispose()
    pending.pop(0)[1]()
    assert host.offset == 30.0, "the next step never runs"


@pytest.mark.parametrize('flipped', [True, False])
def test_cocoa_measures_without_moving_buttons_already_in_place(flipped):
    # every page layout measures again; a move to where a button already is makes AppKit reset the pointer, which
    # flickered against the page's game pointer (alpha.19)
    host, pending, _ = cocoa_motion(flipped)
    host.measure({'centre': 22})
    host.slide([40.0], 1 / 60)
    moves, hides = [], []
    for button in host.buttons:
        original_move, original_hide = button.setFrameOrigin_, button.setHidden_
        button.setFrameOrigin_ = lambda point, f=original_move: (moves.append(point), f(point))
        button.setHidden_ = lambda value, f=original_hide: (hides.append(value), f(value))
    for _ in range(5):
        assert host.measure({'centre': 22}) is not None
    assert moves == [] and hides == []
    host.slide([40.0], 1 / 60)
    assert moves == [] and hides == [], "the same offset again moves nothing"
    host.measure({'centre': 25})
    assert len(moves) == 6 and tops(host) == pytest.approx([-22] * 3), "a new centre line aligns them, the offset stays"


def test_pointer_probe_logs_a_flicker_and_a_system_pointer_that_stays(caplog):
    from ba_modding_toolkit.webui.window_macos import PointerProbe
    arrow, hand, image = object(), object(), object()
    current, clock = [image], [0.0]
    api = NS(NSCursor=NS(currentCursor=lambda: current[0], arrowCursor=lambda: arrow, openHandCursor=lambda: hand,
                         closedHandCursor=lambda: object(), IBeamCursor=lambda: object(), pointingHandCursor=lambda: object()))
    probe = PointerProbe(api, clock=lambda: clock[0])
    with caplog.at_level("INFO", logger="ba_modding_toolkit.webui.window_macos"):
        probe.moved(300, True)
        for step in range(9):  # the two pointers taking turns
            clock[0] += .2
            current[0] = arrow if step % 2 == 0 else image
            probe.moved(300, True)
        current[0] = hand
        for _ in range(30):  # the open hand stays over the page
            clock[0] += .25
            probe.moved(400, True)
        current[0] = image
        for _ in range(30):  # the game pointer stays: nothing to say
            clock[0] += .25
            probe.moved(400, True)
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == "Native pointer changed 8 times in 10 s, now image"
    assert lines[1] == "Native pointer: open hand for 5 s over the page (y 400)"
    assert len(lines) == 2
    assert probe.kind_of(None) == "none" and probe.kind_of(image) == "image"


def test_cocoa_pointer_log_never_stands_in_the_way_of_a_move(cocoa_drag):
    host = cocoa_drag.host
    host.probe = NS(moved=lambda y, over: (_ for _ in ()).throw(RuntimeError("probe")))
    event = cocoa_drag.event()
    assert host.pointer_moved(event) is event


def test_toolbar_layouts_in_a_burst_are_logged(caplog, monkeypatch):
    import ba_modding_toolkit.webui.window_chrome as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    chrome = WindowChrome(NS(), EventBus(), platform='darwin', host=Host())
    regions = [[{"left": 90, "top": 0, "right": 150 + i % 2, "bottom": 44}] for i in range(40)]
    with caplog.at_level("INFO", logger="ba_modding_toolkit.webui.window_chrome"):
        for drag in regions:
            clock[0] += .1
            chrome.update(22, 44, drag)
    lines = [r.getMessage() for r in caplog.records if "toolbar layouts" in r.getMessage()]
    assert lines == ["Native window chrome: 30 toolbar layouts in 10 s"]
