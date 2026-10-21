import { Fragment } from "react";
import type { ReactNode } from "react";
import type { Errors, FieldMeta, Values } from "../types";
import { Field } from "./Field";

export type RenderField = (field: FieldMeta) => ReactNode;

// Splits a section's fields into groups: each group takes the fields its test matches and no earlier group
// took. Fields no group takes end up in a last group, so a field the roles add is never lost.
export function groupFields(fields: FieldMeta[], groups: { legend: string; test: (f: FieldMeta) => boolean }[]) {
  const used = new Set<string>();
  const result = groups.map(({ legend, test }) => {
    const mine = fields.filter((f) => !used.has(f.name) && test(f));
    mine.forEach((f) => used.add(f.name));
    return { legend, fields: mine };
  });
  const rest = fields.filter((f) => !used.has(f.name));
  if (rest.length) result.push({ legend: "Other settings", fields: rest });
  return result.filter((g) => g.fields.length);
}

export function named(...names: string[]) {
  return (f: FieldMeta) => names.includes(f.name);
}

// A group of fields under a legend; its advanced fields go into "More settings", which opens by itself when
// one of them has an error.
export function FieldSet({ legend, fields, values, errors, render, children, after }: {
  legend: string;
  fields: FieldMeta[];
  values: Values;
  errors: Errors;
  render?: RenderField;
  children?: ReactNode; // after the group's ordinary fields
  after?: ReactNode; // after "More settings"
}) {
  const draw = render ?? ((f: FieldMeta) => <Field field={f} values={values} error={errors[f.name]} />);
  const plain = fields.filter((f) => !f.advanced);
  const advanced = fields.filter((f) => f.advanced);
  const open = advanced.some((f) => errors[f.name]);
  return (
    <fieldset className="card-body fieldset">
      <legend>{legend}</legend>
      <div className="fields">
        {plain.map((f) => <Fragment key={f.name}>{draw(f)}</Fragment>)}
        {children}
        {advanced.length ? (
          <details open={open || undefined}>
            <summary>More settings</summary>
            <div className="fields">
              {advanced.map((f) => <Fragment key={f.name}>{draw(f)}</Fragment>)}
            </div>
          </details>
        ) : null}
      </div>
      {after ? <div className="fieldset-after">{after}</div> : null}
    </fieldset>
  );
}
