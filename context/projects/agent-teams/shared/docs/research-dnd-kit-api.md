---
decay_class: perishable
decay_after: 2026-08-10
decay_trigger: "Kanban #772 closes AND dnd-kit version pinned in web/package.json"
promoted_at: 2026-05-12
promoted_from: _scratch/research-dnd-kit-api.md
produced_by: dev-researcher (Kanban #812 smoke gate)
---

# Research — dnd-kit DragDropContext API

## Brief

Summarized dnd-kit's `DndContext` setup, props, event handlers, and the Sortable preset (`SortableContext` + `useSortable`) for a Kanban lane card-reordering feature. This covers the core API needed for implementing drag-and-drop card sorting within a single lane.

## Findings

### DndContext — The Root Container

`DndContext` is a React context provider that wraps all draggable and droppable elements. Nesting is required: "In order for your Droppable and Draggable components to interact with each other, you'll need to make sure that the part of your React tree that uses them is nested within a parent `<DndContext>` component." [Source: https://github.com/dnd-kit/docs/blob/master/api-documentation/context-provider/README.md]

#### Full Props Interface

```typescript
export interface Props {
  id?: string;
  accessibility?: {
    announcements?: Announcements;
    container?: Element;
    restoreFocus?: boolean;
    screenReaderInstructions?: ScreenReaderInstructions;
  };
  autoScroll?: boolean | AutoScrollOptions;
  cancelDrop?: CancelDrop;
  children?: React.ReactNode;
  collisionDetection?: CollisionDetection;
  measuring?: MeasuringConfiguration;
  modifiers?: Modifiers;
  sensors?: SensorDescriptor<any>[];
  onDragAbort?(event: DragAbortEvent): void;
  onDragPending?(event: DragPendingEvent): void;
  onDragStart?(event: DragStartEvent): void;
  onDragMove?(event: DragMoveEvent): void;
  onDragOver?(event: DragOverEvent): void;
  onDragEnd?(event: DragEndEvent): void;
  onDragCancel?(event: DragCancelEvent): void;
}
```
[Source: https://github.com/clauderic/dnd-kit/blob/master/packages/core/src/components/DndContext/DndContext.tsx]

#### Key Props for Kanban

- **`sensors`** — Array of sensor instances (created via `useSensor`). Pointer and Keyboard sensors are **used by default**. [Source: https://dndkit.com/api-documentation/sensors]
- **`collisionDetection`** — Algorithm for detecting which droppable a dragged item is over (e.g., `closestCenter`). The library provides multiple collision algorithms. [Source: https://dndkit.com/api-documentation/sensors]
- **`onDragStart`** — Fires when activation constraints are met (e.g., pointer movement > threshold).
- **`onDragMove`** — Fires as the item moves.
- **`onDragOver`** — Triggers when hovering over droppables.
- **`onDragEnd`** — Completes the operation; **does NOT automatically reorder items**. The consumer must handle reordering logic. [Source: https://github.com/dnd-kit/docs/blob/master/api-documentation/context-provider/README.md]
- **`onDragCancel`** — Handles cancellation (e.g., ESC key or `cancelDrop` returning `true`).
- **`autoScroll`** — Enable/disable auto-scrolling when dragging near container edges. [Source: https://github.com/clauderic/dnd-kit/blob/master/packages/core/src/components/DndContext/DndContext.tsx]
- **`accessibility`** — Screen reader announcements, focus management. [Source: https://github.com/clauderic/dnd-kit/blob/master/packages/core/src/components/DndContext/DndContext.tsx]

### Sensors — Input Method Detection

Four built-in sensors are available: Pointer, Mouse, Touch, and Keyboard. [Source: https://dndkit.com/api-documentation/sensors]

**Default behavior:** Pointer and Keyboard sensors are included automatically.

**Custom sensor setup** via `useSensor` hook:

```javascript
const mouseSensor = useSensor(MouseSensor, {
  activationConstraint: {
    distance: 10,  // pixels the pointer must move before activation
  },
});

// Then pass to DndContext:
<DndContext sensors={[mouseSensor, ...]} />
```

**Activation constraint options:**
- `distance` — pixels of pointer movement required
- `delay` — milliseconds before activation begins
- `tolerance` — pixels allowed while waiting to activate

[Source: https://dndkit.com/api-documentation/sensors]

### SortableContext — The Sortable Preset

`SortableContext` is a context provider that supplies metadata consumed by the `useSortable` hook. It must be nested within a `DndContext`.

**Required prop:**
- **`items`** — A sorted array of unique identifiers matching the rendered items. "Make sure that the `items` prop passed to `SortableContext` be sorted in the same order in which the items are rendered, otherwise you may see unexpected results." [Source: https://dndkit.com/presets/sortable]

**Optional props:**
- **`strategy`** — Sorting strategy that determines how transforms are calculated:
  - `rectSortingStrategy` (default) — suitable for most cases, does NOT support virtualized lists.
  - `verticalListSortingStrategy` — optimized for vertical lists; supports virtualization.
  - `horizontalListSortingStrategy` — optimized for horizontal lists; supports virtualization.
  - `rectSwappingStrategy` — enables swappable functionality.
  - Custom strategies can be created for specialized layouts.

[Source: https://github.com/dnd-kit/docs/blob/master/presets/sortable/sortable-context.md] [Source: https://dndkit.com/presets/sortable]

- **`id`** — Optional identifier for the context. Auto-generated if omitted. Useful for custom sensors.

**Multiple SortableContext instances:** Can be nested or siblings to support multi-lane drag operations (e.g., moving cards between lanes). [Source: https://github.com/dnd-kit/docs/blob/master/presets/sortable/sortable-context.md]

### useSortable Hook — Per-Item Binding

The `useSortable` hook combines `useDraggable` and `useDroppable`, making each item both draggable and droppable.

**Return object includes:**

```typescript
{
  active: DraggableEntry | null;
  activeIndex: number;
  attributes: DOMAttributes;
  data: Record<string, any>;
  rect: ClientRect | null;
  index: number;
  newIndex: number;
  items: UniqueIdentifier[];
  isOver: boolean;
  isSorting: boolean;
  isDragging: boolean;
  listeners: SyntheticListenerMap;
  node: HTMLElement | null;
  overIndex: number;
  over: DroppableContainer | null;
  setNodeRef: (node: HTMLElement | null) => void;
  setActivatorNodeRef: (node: HTMLElement | null) => void;
  setDroppableNodeRef: (node: HTMLElement | null) => void;
  setDraggableNodeRef: (node: HTMLElement | null) => void;
  transform: Transform | null;
  transition: string | null;
}
```

[Source: https://github.com/clauderic/dnd-kit/blob/master/packages/sortable/src/hooks/useSortable.ts]

**Key properties for Kanban:**
- **`setNodeRef`** — Attach to the DOM element; required for measurements and animations.
- **`listeners`** — Event handlers for drag interactions (spread onto the draggable DOM node).
- **`attributes`** — Accessibility attributes (a11y role, aria-* props).
- **`isDragging`** — True while the item is being dragged; useful for visual feedback.
- **`isOver`** — True when the item is over another sortable item.
- **`transform`** — CSS transform for positioning animations. Paired with `transition` for smooth motion. Default transition is 250ms ease. [Source: https://dndkit.com/presets/sortable]
- **`transition`** — CSS transition property for animations.
- **`index`** — Current rendered index.
- **`newIndex`** — Calculated new index after sorting (only meaningful during a drag).
- **`over`** — The item currently being dragged over.

### The onDragEnd Handler — Critical for Kanban

**Key insight:** `onDragEnd` does NOT automatically reorder items. [Source: https://github.com/dnd-kit/docs/blob/master/api-documentation/context-provider/README.md]

The event receives `{ active, over }` where:
- `active` — the item being dragged (has `.id`).
- `over` — the drop target item (has `.id` if over a droppable).

**Reordering pattern:**

```jsx
function handleDragEnd(event) {
  const { active, over } = event;

  if (active.id !== over?.id) {
    setItems((items) => {
      const oldIndex = items.findIndex(i => i.id === active.id);
      const newIndex = items.findIndex(i => i.id === over.id);
      return arrayMove(items, oldIndex, newIndex);
    });
  }
}
```

Use `arrayMove` (exported from `@dnd-kit/sortable`) or implement custom reordering logic. [Source: https://dev.to/shubhadip/getting-started-with-react-dnd-kit-3djb]

### Minimal Kanban Example

```jsx
import { DndContext, closestCenter, useSensor, MouseSensor, TouchSensor } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy, useSortable, arrayMove } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

function Card({ id, title }) {
  const { setNodeRef, transform, transition, isDragging } = useSortable({ id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return <div ref={setNodeRef} style={style}>{title}</div>;
}

function Lane({ cards, onReorder }) {
  const sensors = useSensor(MouseSensor) && useSensor(TouchSensor);

  return (
    <DndContext
      sensors={[sensors]}
      collisionDetection={closestCenter}
      onDragEnd={(e) => {
        const { active, over } = e;
        if (active.id !== over?.id) {
          const oldIdx = cards.findIndex(c => c.id === active.id);
          const newIdx = cards.findIndex(c => c.id === over.id);
          onReorder(arrayMove(cards, oldIdx, newIdx));
        }
      }}
    >
      <SortableContext items={cards.map(c => c.id)} strategy={verticalListSortingStrategy}>
        {cards.map(card => <Card key={card.id} id={card.id} title={card.title} />)}
      </SortableContext>
    </DndContext>
  );
}
```

## Trade-offs / open variants

1. **Collision detection algorithm choice:** `closestCenter` is the default in many examples, but dnd-kit offers others (not documented in URLs fetched). For Kanban lanes with variable-height cards, `closestCenter` is typically appropriate. Verify against the collision-detection API docs if custom behavior is needed.

2. **Cross-lane moves:** The example above handles within-lane sorting. For moving cards between lanes, wrap multiple `SortableContext` instances in one `DndContext` and check `over?.data?.sortableData?.containerId` in `onDragEnd` to determine target lane. This is a documented pattern but examples were limited in fetches.

3. **Strategy selection:** `verticalListSortingStrategy` is recommended for a vertical Kanban lane (improves performance for virtualized lists). `rectSortingStrategy` is simpler but doesn't support virtualization.

4. **Optimistic updates:** The example above reorders state on `onDragEnd` (not during drag). For a Kanban with server sync, consider:
   - Optimistic reordering on `onDragEnd` + async save to backend.
   - Revert on save failure.

## Source URLs

- https://github.com/clauderic/dnd-kit/blob/master/packages/core/src/components/DndContext/DndContext.tsx — accessed 2026-05-12 — Full DndContext Props interface and TypeScript signatures.
- https://github.com/dnd-kit/docs/blob/master/api-documentation/context-provider/README.md — accessed 2026-05-12 — DndContext documentation, core purpose, event handlers, and features.
- https://dndkit.com/api-documentation/sensors — accessed 2026-05-12 — Sensor types, useSensor hook, activation constraints.
- https://dndkit.com/presets/sortable — accessed 2026-05-12 — SortableContext strategy prop, sorting strategies overview, useSortable return shape.
- https://github.com/dnd-kit/docs/blob/master/presets/sortable/sortable-context.md — accessed 2026-05-12 — SortableContext Props (items, strategy, id), required sorting order, virtualization support, nesting.
- https://github.com/clauderic/dnd-kit/blob/master/packages/sortable/src/hooks/useSortable.ts — accessed 2026-05-12 — useSortable return type and all properties.
- https://dev.to/shubhadip/getting-started-with-react-dnd-kit-3djb — accessed 2026-05-12 — Practical example of DndContext + SortableContext + handleDragEnd with arrayMove.
- https://medium.com/@anish_ali/how-to-use-dnd-kit-in-react-js-833cc6866c6d — accessed 2026-05-12 — DndContext setup structure, sensors placeholder, practical patterns.

## Open questions

1. **Collision detection algorithms:** The `/api-documentation/collision-detection` URL returned 404. Examples reference `closestCenter` but the full list of algorithms and their trade-offs is not documented in fetched sources. Recommend frontend specialist consult the GitHub source or ask dnd-kit maintainers for latest algorithms.

2. **Cross-lane moves implementation details:** Multiple `SortableContext` examples exist (e.g., GitHub discussion #1111) but were not fetched. The exact `over?.data?.sortableData?.containerId` pattern may have evolved. Frontend specialist should verify against current dnd-kit examples when implementing multi-lane drag.

3. **Server-side optimistic update patterns:** No canonical dnd-kit pattern found for optimistic updates with rollback. Specialist should decide: update state immediately on drop, or wait for server confirmation.

## Suggested next steps for Lead

- **Frontend specialist spawn brief:** Include the minimal Kanban example above, the Props interface, and the SortableContext strategy guidance. Point them to the GitHub source repo if they need collision-detection details or cross-lane patterns.
- **Kanban #772 acceptance criteria should include:** Cards reorder within a lane on drop, `isDragging` visual feedback, keyboard support (tested via Keyboard sensor), and tested cross-lane prevention (via `over?.id` check in current lane context).
- **Future research:** If task escalates to multi-lane, cross-container moves, or virtual scrolling, spawn researcher again for those specific use cases.
