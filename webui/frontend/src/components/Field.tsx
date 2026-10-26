// A form field drawn from the server's description of it (schema.py): its label, help, default and variable
// name, the value saved, and the server's error for it. Inputs are uncontrolled: what the user types stays in
// the form until a save succeeds and the page loads the saved values again.

import type { ReactNode } from "react";
import type { Cluster, FieldMeta, Values } from "../types";
import { Icon } from "./Icons";

export function fieldId(name: string): string {
  return `f-${name}`;
}

export function FieldError({ id, error }: { id: string; error?: string }) {
  if (!error) return null;
  return (
    <p className="error" id={`${id}-error`}>
      <Icon name="alert" />
      <span><span className="visually-hidden">Error: </span>{error}</span>
    </p>
  );
}

function Help({ field, id, children }: { field: FieldMeta; id: string; children?: ReactNode }) {
  return (
    <p className="help" id={`${id}-help`}>
      {children ?? (field.help ? `${field.help} ` : null)}
      <span>
        Variable <code>{field.variable}</code>
        {field.default !== null ? <>, default <code>{field.default}</code></> : null}.
      </span>
    </p>
  );
}

function describedBy(id: string, error?: string, extra: string[] = []): string {
  return [`${id}-help`, ...(error ? [`${id}-error`] : []), ...extra].join(" ");
}

function Required({ field }: { field: FieldMeta }) {
  return field.required && !field.saved ? <span className="req"> (required)</span> : null;
}

const WIDE = new Set(["list"]);

export function Field({ field, values, error, list }: {
  field: FieldMeta;
  values: Values;
  error?: string;
  list?: string; // the id of a <datalist> of suggestions
}) {
  const id = fieldId(field.name);
  const value = values[field.name];
  const invalid = error ? true : undefined;

  if (field.kind === "bool") {
    return (
      <div className="check-field">
        <div className="check">
          <input type="checkbox" id={id} name={field.name} defaultChecked={value === true}
                 aria-describedby={describedBy(id, error)} aria-invalid={invalid} />
          <label htmlFor={id}>{field.label}</label>
        </div>
        <Help field={field} id={id} />
        <FieldError id={id} error={error} />
      </div>
    );
  }

  let control: ReactNode;
  if (field.kind === "choice") {
    control = (
      <div className="select">
        <select id={id} name={field.name} defaultValue={String(value ?? "")} aria-describedby={describedBy(id, error)}
                aria-invalid={invalid}>
          {(field.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>{field.choice_labels?.[choice] ?? choice}</option>
          ))}
        </select>
      </div>
    );
  } else if (field.kind === "list") {
    const rows = field.name === "admin_extra_ssh_keys" || field.name === "firewall_allow" ? 4 : 3;
    control = (
      <textarea id={id} name={field.name} rows={rows} spellCheck={false} defaultValue={String(value ?? "")}
                aria-describedby={describedBy(id, error)} aria-invalid={invalid} />
    );
  } else if (field.kind === "secret") {
    control = (
      <>
        <input type="password" id={id} name={field.name} autoComplete="new-password" spellCheck={false}
               aria-describedby={describedBy(id, error, [`${id}-saved`])} aria-invalid={invalid} />
        <p className="saved" id={`${id}-saved`}>
          {field.saved ? "A value is saved. Leave this empty to keep it." : "Not set yet."}
        </p>
        {field.saved && !field.required ? (
          <div className="check">
            <input type="checkbox" id={`${id}-clear`} name={`${field.name}__clear`} />
            <label htmlFor={`${id}-clear`}>Remove the saved value</label>
          </div>
        ) : null}
      </>
    );
  } else {
    control = (
      <input type="text" id={id} name={field.name} defaultValue={String(value ?? "")} className="mono"
             inputMode={field.kind === "int" ? "numeric" : undefined} list={list} spellCheck={false}
             autoComplete="off" aria-describedby={describedBy(id, error)} aria-invalid={invalid} />
    );
  }
  return (
    <div className={`field${WIDE.has(field.kind) ? " wide" : ""}`}>
      <label htmlFor={id}>{field.label}<Required field={field} /></label>
      {control}
      <Help field={field} id={id} />
      <FieldError id={id} error={error} />
    </div>
  );
}

// The Proxmox node as a dropdown of the cluster's nodes from the last connection test.
export function NodeField({ field, values, error, cluster }: {
  field: FieldMeta;
  values: Values;
  error?: string;
  cluster: Cluster;
}) {
  const id = fieldId(field.name);
  const value = String(values[field.name] ?? "");
  const nodes = cluster.nodes ?? {};
  return (
    <div className="field">
      <label htmlFor={id}>{field.label} <span className="req">(required)</span></label>
      <div className="select">
        <select id={id} name={field.name} defaultValue={value} aria-describedby={describedBy(id, error)}
                aria-invalid={error ? true : undefined}>
          {!value ? <option value="" disabled>Choose a node</option> : null}
          {value && !(value in nodes) ? <option value={value}>{value} (not in the cluster)</option> : null}
          {Object.keys(nodes).sort().map((name) => (
            <option key={name} value={name}>{name} ({nodes[name]})</option>
          ))}
        </select>
      </div>
      <Help field={field} id={id}>
        {cluster.name ? <>The nodes of cluster <strong>{cluster.name}</strong></> : "The nodes the API reports"},
        from the last connection test; test again to refresh the list. {field.help}{" "}
      </Help>
      <FieldError id={id} error={error} />
    </div>
  );
}

// The ISO storage as a dropdown of the storages that allow ISO images, from the last connection test.
export function IsoStorageField({ field, values, error, cluster }: {
  field: FieldMeta;
  values: Values;
  error?: string;
  cluster: Cluster;
}) {
  const id = fieldId(field.name);
  const value = String(values[field.name] ?? "");
  const storages = cluster.iso_storages ?? {};
  return (
    <div className="field">
      <label htmlFor={id}>{field.label}</label>
      <div className="select">
        <select id={id} name={field.name} defaultValue={value} aria-describedby={describedBy(id, error)}
                aria-invalid={error ? true : undefined}>
          {value && !(value in storages) ? <option value={value}>{value} (doesn't allow ISO images)</option> : null}
          {Object.keys(storages).sort().map((name) => (
            <option key={name} value={name}>{name} ({storages[name]})</option>
          ))}
        </select>
      </div>
      <Help field={field} id={id}>
        The storages that allow ISO images, from the last connection test. {field.help}{" "}
      </Help>
      <FieldError id={id} error={error} />
    </div>
  );
}
