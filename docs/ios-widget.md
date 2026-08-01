# Official Home Assistant iOS widget

Household Tasks integrates with the **Custom Widget (BETA)** included in the
official Home Assistant Companion App. It requires no Apple Developer account,
no separate access token, and no Scriptable installation.

This guide deliberately separates the two configuration stages that iOS makes
easy to confuse:

1. create and populate a widget configuration inside the Home Assistant app;
2. add a Home Assistant widget to the iOS Home Screen and select that saved
   configuration.

The four recipes below list every item in the order in which it must be added.

## What can and cannot match the concept images

The images in this guide are design concepts. The official Custom Widget uses
an automatic grid of equal-sized entity tiles. The current editor lets you
choose the entity, item order, icon, colors, display text, tap action, and
confirmation behavior. It does **not** provide arbitrary row spans, free-form
headers, avatars, badges, or typography.

Consequently, the recipes reproduce the behavior and information hierarchy of
the images, not their pixel-perfect geometry:

- item order determines the grid position;
- iOS determines the number of visible items for the selected widget size;
- a wide “next task” row becomes a normal entity tile;
- labels such as “once per season” are explanatory text in the image, not a
  separate entity supplied by Household Tasks;
- a shared action tile must be replaced by one action tile per person.

Use a **Large** system widget for the complete recipes. If the selected iOS or
Companion App version does not show every item, create two smaller widget
configurations instead of dropping an action tile.

## 1. Verify the prerequisites in Home Assistant

Do this before opening the widget editor. Missing server-side entities cannot
be repaired from the iPhone widget screen.

1. Install and open the official Home Assistant Companion App on the iPhone.
2. Connect the app to the same Home Assistant server on which Household Tasks
   is installed and allow notifications.
3. In **Household Tasks > People**, create or edit the intended person.
4. Select the matching Home Assistant user.
5. Select that iPhone's `notify.mobile_app_*` action and save the person.
6. Open **Settings > Devices & services > Entities** in Home Assistant.
7. Filter the list by the **Household Tasks** integration.

For a person called Alex, expect entities similar to these:

| Purpose | Typical display name | Example entity ID |
| --- | --- | --- |
| Legacy personal inbox | `Alex task inbox` | `sensor.household_tasks_alex` |
| Personal next task | `Alex next task` | `sensor.household_tasks_alex_next_task` |
| Read-only task list | `Alex task 1` through `Alex task 5` | `sensor.household_tasks_alex_next_task_1` through `_5` |
| Personal open count | `Alex open tasks` | `sensor.household_tasks_alex_open` |
| Personal due-today count | `Alex tasks due today` | `sensor.household_tasks_alex_due_today` |
| Personal overdue count | `Alex overdue tasks` | `sensor.household_tasks_alex_overdue` |
| Personal actions | `Alex task actions` | `button.household_tasks_alex_actions` |
| All active tasks | `Open tasks` | `sensor.open_tasks` |
| Due today | `Tasks due today` | `sensor.tasks_due_today` |
| Overdue | `Overdue tasks` | `sensor.overdue_tasks` |
| Blocked | `Blocked tasks` | `sensor.blocked_tasks` |

Entity IDs are examples. Home Assistant may append a suffix when an ID already
exists. Copy the exact IDs from the entity list instead of typing the examples
blindly.

If the personal sensor and button do not exist, confirm that the person is
actually saved in Household Tasks, then reload the Household Tasks integration
or restart Home Assistant. The integration creates no personal widget entities
while the Household Tasks people list is empty.

The Companion App keeps its own entity database for widget configuration. If
new entities exist under **Developer tools > States** but not in the widget
picker, open the production server under **Settings > Companion App** and tap
**Update server information**. Close and reopen the widget editor afterwards.

## 2. Create a reusable widget configuration in the app

The wording can vary slightly with the app language, but the path and fields
are the same.

1. Open the Home Assistant app on the iPhone.
2. Open **Settings > Companion App > Widgets**.
3. Under **Custom Widgets (BETA)**, tap **Create**.
4. Give the configuration a recognizable name, for example `Household – My
   day`.
5. Tap **Add item**.
6. Select the entity listed in the chosen recipe below.
7. Open that item and configure **Display text**, **Icon**, **Icon color**,
   optional custom colors, **On tap**, and **Require confirmation** exactly as
   shown in the recipe.
8. Tap **Add** to return to the item list.
9. Repeat for every recipe row, from top to bottom.
10. Reorder items with the drag handles if necessary. The first item occupies
    the first grid position.
11. Tap **Save**.

### Tap-action reference

| Tap action | Use it for | Result |
| --- | --- | --- |
| **Default** | A `button.*task_actions` entity | Presses the button and asks Household Tasks to send fresh actions for the current task. |
| **Navigate** | Inbox and count sensors | Opens `/haushaltsaufgaben` in the Home Assistant app. |
| **Nothing** | Display-only sensor tile | Refreshes the widget without opening another screen. |
| **Run script** | An existing HA script | Runs the selected script; this is not required by the standard recipes. |

Use confirmation for action buttons when accidental taps matter. Confirmation
is unnecessary for read-only sensors and navigation tiles.

## 3. Add the saved configuration to the iOS Home Screen

Saving the configuration in the Companion App does not add it to the Home
Screen automatically.

1. Return to the iOS Home Screen.
2. Long-press an empty area until the icons start moving.
3. Tap **+**, search for **Home Assistant**, and select it.
4. Swipe to the **Custom Widget** in the desired size. Choose **Large** for the
   complete recipes below.
5. Tap **Add Widget**.
6. Long-press the new widget and choose **Edit Widget**.
7. In **Widget**, select the configuration saved in the previous section.
8. Enable **Show states** so task titles and counts are visible.
9. Enable **Show last update time** only if the additional footer is useful.
10. Leave edit mode and tap each tile once to verify its behavior.

If the widget shows fewer items than configured, the selected size has reached
its item limit. Use a larger widget or split the recipe into two configurations.

## Recipe A: My day

![My day widget concept](images/ios-widget-my-day.webp)

Recommended configuration name: `Household – My day`

For a real read-only task preview, add the five stable task positions followed
by the action button. The entity IDs never change when tasks are completed or
reordered; only their displayed states change.

Add these six items in this exact order:

| # | Entity | Display text | Icon | Color | On tap | Confirmation |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `sensor.household_tasks_alex_next_task_1` | `1` | `mdi:numeric-1-circle-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 2 | `sensor.household_tasks_alex_next_task_2` | `2` | `mdi:numeric-2-circle-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 3 | `sensor.household_tasks_alex_next_task_3` | `3` | `mdi:numeric-3-circle-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 4 | `sensor.household_tasks_alex_next_task_4` | `4` | `mdi:numeric-4-circle-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 5 | `sensor.household_tasks_alex_next_task_5` | `5` | `mdi:numeric-5-circle-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 6 | `button.household_tasks_alex_actions` | `Actions` | `mdi:bell-outline` | Blue | **Default** | On |

Replace `alex` with the real person entity IDs. Each numbered sensor displays
the task title at that current feed position as its state. An unused position
shows a neutral dash. The action button sends an actionable notification for the
current first task; it does not complete a task immediately.

The concept image shows the first item as a wide row. The official widget will
render it as the first normal grid tile.

## Recipe B: Household status

![Household status widget concept](images/ios-widget-household-status.webp)

Recommended configuration name: `Household – Status`

Add these four items in this exact order:

| # | Entity | Display text | Icon | Color | On tap | Confirmation |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `sensor.open_tasks` | `Open` | `mdi:clipboard-text-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 2 | `sensor.tasks_due_today` | `Today` | `mdi:calendar-today` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 3 | `sensor.overdue_tasks` | `Overdue` | `mdi:clock-alert-outline` | Red | **Navigate** → `/haushaltsaufgaben` | Off |
| 4 | `sensor.blocked_tasks` | `Blocked` | `mdi:lock-outline` | Amber | **Navigate** → `/haushaltsaufgaben` | Off |

This is the easiest recipe to reproduce because it uses exactly four uniform
tiles and contains no personal task title.

## Recipe C: Season and weather

![Season and weather widget concept](images/ios-widget-season-weather.webp)

Recommended configuration name: `Household – Weather`

The official widget can display only entity states. To reproduce the
“temperature tomorrow” tile, select an existing forecast sensor from the local
weather integration or create a Home Assistant template sensor that exposes
tomorrow's minimum temperature. Household Tasks does not create this weather
sensor.

Add these five items in this exact order:

| # | Entity | Display text | Icon | Color | On tap | Confirmation |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Your forecast temperature sensor | `Tomorrow` | `mdi:thermometer-low` | Blue | **Nothing** | Off |
| 2 | `sensor.household_tasks_alex` | `Alex` | `mdi:car-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 3 | `sensor.household_tasks_sam` | `Sam` | `mdi:car-outline` | Green | **Navigate** → `/haushaltsaufgaben` | Off |
| 4 | `button.household_tasks_alex_actions` | `Alex actions` | `mdi:bell-outline` | Blue | **Default** | On |
| 5 | `button.household_tasks_sam_actions` | `Sam actions` | `mdi:bell-outline` | Green | **Default** | On |

Omit item 1 if no suitable forecast sensor exists. The “once per season” badge
in the concept image is enforced by the Household Tasks rule engine but is not
currently exposed as a separate widget entity.

There is intentionally no shared action button: each generated action button
is bound to one person's inbox and notification destination.

## Recipe D: Family actions

![Family actions widget concept](images/ios-widget-family-actions.webp)

Recommended configuration name: `Household – Family`

Add these four items in this exact order:

| # | Entity | Display text | Icon | Color | On tap | Confirmation |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `sensor.household_tasks_alex` | `Alex` | `mdi:clipboard-account-outline` | Blue | **Navigate** → `/haushaltsaufgaben` | Off |
| 2 | `button.household_tasks_alex_actions` | `Alex actions` | `mdi:bell-outline` | Blue | **Default** | On |
| 3 | `sensor.household_tasks_sam` | `Sam` | `mdi:clipboard-account-outline` | Green | **Navigate** → `/haushaltsaufgaben` | Off |
| 4 | `button.household_tasks_sam_actions` | `Sam actions` | `mdi:bell-outline` | Green | **Default** | On |

The official grid produces two functional person lanes when it places items 1
and 2 in the first row and items 3 and 4 in the second. It cannot render the
large circular avatars shown in the concept image.

## What happens when an action tile is tapped

The action tile presses the person's Household Tasks button. The server selects
the current next task and sends a fresh actionable notification. Depending on
the task state, that notification can offer actions such as **Complete**,
**This evening**, **Tomorrow**, **Claim**, or **Request help**.

These notification actions use the immutable task occurrence ID. A delayed tap
therefore cannot accidentally modify a different task that has since moved to
the top of the person's inbox.

## Refresh behavior

iOS controls widget refresh scheduling and may display a cached sensor value
for roughly 15 minutes. The action button still selects the current task on the
server, regardless of the title currently cached by the widget.

For important changes, an optional automation can request a widget refresh:

```yaml
alias: Refresh Household Tasks iOS widget
mode: restart
triggers:
  - trigger: event
    event_type: household_tasks_updated
actions:
  - delay: "00:00:05"
  - action: notify.mobile_app_iphone
    data:
      message: update_widgets
```

Replace `notify.mobile_app_iphone` with the actual Companion App notification
action. iOS can still defer updates, and excessive refresh requests increase
battery use.

## Troubleshooting checklist

### The personal inbox or action entity is missing in Home Assistant

- Confirm that at least one person is saved under **Household Tasks > People**.
- Confirm that the person is linked to a Home Assistant user.
- Reload Household Tasks or restart Home Assistant.
- Search the complete entity list by integration, not only by an assumed entity
  ID.

### The entities exist in Home Assistant but not in the app editor

- Open the Home Assistant app and select the correct server.
- Pull to refresh or completely close and reopen the app.
- Reopen **Settings > Companion App > Widgets** and create a new configuration.
- Confirm that the Home Assistant user can access the personal sensor and
  button entities.

### The action tile is unavailable

- Open the Companion App once and allow notifications.
- Confirm that the person's configured `notify.mobile_app_*` action still
  exists.
- Restart Home Assistant after registering a new mobile device.

### Tapping a tile opens the wrong screen

- Use **Default** only for the `button.*task_actions` entity.
- Use **Navigate** with `/haushaltsaufgaben` for inbox and count sensors.
- Use **Nothing** for a display-only weather tile.

### The layout does not match the image

- Confirm the items are in the documented order.
- Use the Large system widget.
- Remember that iOS controls the uniform grid; wide rows, avatars, headers, and
  badges in the concept images are not configurable in the official widget.

The current widget editor and supported sizes are documented in the
[official Home Assistant Companion documentation](https://companion.home-assistant.io/docs/integrations/ios-widgets/).
Interactive notification behavior is documented under
[Actionable Notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/).
