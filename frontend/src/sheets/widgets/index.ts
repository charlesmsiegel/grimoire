import type { ComponentType } from "react";
import type { WidgetName, WidgetProps } from "../types";
import { TextWidget } from "./Text";
import { TextareaWidget } from "./Textarea";
import { NumberWidget } from "./NumberInput";
import { SelectWidget } from "./Select";
import { MultiSelectWidget } from "./MultiSelect";
import { BooleanWidget } from "./BooleanInput";
import { DotRatingWidget } from "./DotRating";
import { DicePoolWidget } from "./DicePool";
import { HealthTrackWidget } from "./HealthTrack";
import { PowerListWidget } from "./PowerList";
import { GridRatingWidget } from "./GridRating";
import { SlotListWidget } from "./SlotList";
import { KeywordListWidget } from "./KeywordList";
import { NestedSectionWidget } from "./NestedSection";
import { GenericFallbackWidget } from "./GenericFallback";

export type AnyWidget = ComponentType<WidgetProps<never>>;

function asAnyWidget<T>(w: ComponentType<WidgetProps<T>>): AnyWidget {
  return w as unknown as AnyWidget;
}

export const WIDGETS: Record<WidgetName, AnyWidget> = {
  text: asAnyWidget(TextWidget),
  textarea: asAnyWidget(TextareaWidget),
  number: asAnyWidget(NumberWidget),
  select: asAnyWidget(SelectWidget),
  "multi-select": asAnyWidget(MultiSelectWidget),
  boolean: asAnyWidget(BooleanWidget),
  "dot-rating": asAnyWidget(DotRatingWidget),
  "dice-pool": asAnyWidget(DicePoolWidget),
  "health-track": asAnyWidget(HealthTrackWidget),
  "power-list": asAnyWidget(PowerListWidget),
  "grid-rating": asAnyWidget(GridRatingWidget),
  "slot-list": asAnyWidget(SlotListWidget),
  "keyword-list": asAnyWidget(KeywordListWidget),
  "nested-section": asAnyWidget(NestedSectionWidget),
};

export const FallbackWidget: AnyWidget = asAnyWidget(GenericFallbackWidget);

export function resolveWidget(widgetName: string | undefined): AnyWidget {
  if (widgetName && widgetName in WIDGETS) {
    const widget = WIDGETS[widgetName as WidgetName];
    if (widget) return widget;
  }
  return FallbackWidget;
}
