import { useId, useState, type FormEvent } from "react";
import { Alert, Button, Description, Drawer, FieldError, Input, Label, ListBox, Select, Switch, TextField, toast } from "@heroui/react";

import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useCreateLocation, useUpdateLocation } from "@/lib/locations-api";
import type { Location, LocationSaveResponse } from "@/types/locations";
import {
  LOCATION_KINDS,
  buildCreatePayload,
  buildUpdatePayload,
  emptyLocationForm,
  locationToForm,
  validateLocationForm,
  type LocationFormErrors,
  type LocationFormValues,
} from "@/components/admin/locations/location-model";
import { LOCATIONS_COPY, LOCATION_ERROR_CODES } from "@/components/admin/locations/locations-copy";

/** One labelled text field with its rule message linked to the input. */
function FormField({
  label,
  value,
  onChange,
  error,
  errorText,
  description,
  ltr,
  readOnly,
  className,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: boolean;
  errorText?: string;
  description?: string;
  ltr?: boolean;
  readOnly?: boolean;
  className?: string;
}) {
  return (
    <TextField className={className} value={value} onChange={onChange} isInvalid={error} isReadOnly={readOnly}>
      <Label>{label}</Label>
      <Input dir={ltr ? "ltr" : "auto"} />
      {description && <Description>{description}</Description>}
      {error && errorText && <FieldError>{errorText}</FieldError>}
    </TextField>
  );
}

/**
 * The create and edit form of one location. It is mounted only while open, so
 * every open starts from a clean form (prefilled from the row on edit). A
 * refused save keeps it open with every typed value.
 */
export default function LocationDrawer({
  mode,
  location,
  onClose,
  onSaved,
}: {
  mode: "create" | "edit";
  location: Location | null;
  onClose: () => void;
  onSaved: (response: LocationSaveResponse) => void;
}) {
  const { lang, dir } = useI18n();
  const copy = LOCATIONS_COPY[lang];
  const kindId = useId();
  const createMutation = useCreateLocation();
  const updateMutation = useUpdateLocation();
  const pending = mode === "create" ? createMutation.isPending : updateMutation.isPending;
  const error = mode === "create" ? createMutation.error : updateMutation.error;
  const [values, setValues] = useState<LocationFormValues>(() =>
    mode === "edit" && location ? locationToForm(location) : emptyLocationForm(),
  );
  // Validation runs on Save; afterwards the fields revalidate as they change.
  const [submitted, setSubmitted] = useState(false);
  const errors: LocationFormErrors = submitted ? validateLocationForm(values) : {};
  const field = (name: keyof LocationFormValues) => ({
    error: Boolean(errors[name as keyof LocationFormErrors]),
    errorText: errors[name as keyof LocationFormErrors] ? copy.validation[errors[name as keyof LocationFormErrors]!] : undefined,
  });
  const heading = mode === "create" ? copy.createHeading : copy.editHeading;
  // An unchanged edit would be refused with `no_changes`, so it never sends.
  const noChanges =
    submitted &&
    Object.keys(errors).length === 0 &&
    mode === "edit" &&
    location !== null &&
    buildUpdatePayload(values, location) === null;

  function setField<K extends keyof LocationFormValues>(name: K, value: LocationFormValues[K]) {
    setValues((previous) => ({ ...previous, [name]: value }));
  }

  function close() {
    if (pending) return;
    onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setSubmitted(true);
    // Computed fresh: the rendered `errors` predates this press.
    const validation = validateLocationForm(values);
    if (Object.keys(validation).length > 0) return;
    if (mode === "create") {
      createMutation.mutate(buildCreatePayload(values), {
        onSuccess: (response) => {
          toast.success(copy.saved);
          onSaved(response);
          onClose();
        },
      });
    } else if (location) {
      const patch = buildUpdatePayload(values, location);
      if (patch === null) return;
      updateMutation.mutate(
        { id: location.id, patch },
        {
          onSuccess: (response) => {
            toast.success(copy.saved);
            onSaved(response);
            onClose();
          },
        },
      );
    }
  }

  return (
    <Drawer.Backdrop
      isOpen
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
        <Drawer.Dialog aria-label={heading} className="w-[34rem] max-w-[90vw] p-4">
          <Drawer.CloseTrigger aria-label={copy.close} />
          <Drawer.Header>
            <Drawer.Heading level={2}>{heading}</Drawer.Heading>
          </Drawer.Header>
          <form onSubmit={submit} noValidate>
            <Drawer.Body>
              <div className="grid gap-4 sm:grid-cols-2">
                <Select
                  className="w-full"
                  value={values.kind}
                  onChange={(kind) => {
                    if (typeof kind === "string" && (LOCATION_KINDS as readonly string[]).includes(kind)) {
                      setField("kind", kind as LocationFormValues["kind"]);
                    }
                  }}
                  variant="secondary"
                  isDisabled={pending}
                >
                  <Label id={kindId}>{copy.fields.kind}</Label>
                  <Select.Trigger aria-labelledby={kindId}>
                    <Select.Value />
                    <Select.Indicator />
                  </Select.Trigger>
                  <Select.Popover>
                    <ListBox>
                      {LOCATION_KINDS.map((kind) => (
                        <ListBox.Item key={kind} id={kind} textValue={copy.kinds[kind]}>
                          {copy.kinds[kind]}
                          <ListBox.ItemIndicator />
                        </ListBox.Item>
                      ))}
                    </ListBox>
                  </Select.Popover>
                </Select>

                <FormField
                  className="w-full"
                  label={copy.fields.code}
                  value={values.code}
                  onChange={(value) => setField("code", value)}
                  ltr
                  readOnly={pending}
                  {...field("code")}
                />

                <FormField
                  className="w-full sm:col-span-2"
                  label={copy.fields.name}
                  value={values.name}
                  onChange={(value) => setField("name", value)}
                  readOnly={pending}
                  {...field("name")}
                />

                <FormField
                  className="w-full"
                  label={copy.fields.province}
                  value={values.province}
                  onChange={(value) => setField("province", value)}
                  readOnly={pending}
                />
                <FormField
                  className="w-full"
                  label={copy.fields.city}
                  value={values.city}
                  onChange={(value) => setField("city", value)}
                  readOnly={pending}
                />
                <FormField
                  className="w-full"
                  label={copy.fields.district}
                  value={values.district}
                  onChange={(value) => setField("district", value)}
                  readOnly={pending}
                />
                <FormField
                  className="w-full"
                  label={copy.fields.street}
                  value={values.street}
                  onChange={(value) => setField("street", value)}
                  readOnly={pending}
                />
                <FormField
                  className="w-full"
                  label={copy.fields.postalCode}
                  value={values.postalCode}
                  onChange={(value) => setField("postalCode", value)}
                  ltr
                  readOnly={pending}
                  {...field("postalCode")}
                />
                <FormField
                  className="w-full"
                  label={copy.fields.phone}
                  value={values.phone}
                  onChange={(value) => setField("phone", value)}
                  ltr
                  readOnly={pending}
                  {...field("phone")}
                />
                <FormField
                  className="w-full"
                  label={copy.fields.latitude}
                  value={values.latitude}
                  onChange={(value) => setField("latitude", value)}
                  ltr
                  readOnly={pending}
                  {...field("latitude")}
                />
                <FormField
                  className="w-full"
                  label={copy.fields.longitude}
                  value={values.longitude}
                  onChange={(value) => setField("longitude", value)}
                  ltr
                  readOnly={pending}
                  {...field("longitude")}
                />

                <FormField
                  className="w-full sm:col-span-2"
                  label={copy.fields.address}
                  value={values.addressText}
                  onChange={(value) => setField("addressText", value)}
                  readOnly={pending}
                />

                <FormField
                  className="w-full sm:col-span-2"
                  label={copy.fields.serviceTags}
                  value={values.serviceTags}
                  onChange={(value) => setField("serviceTags", value)}
                  description={copy.tagsHelp}
                  readOnly={pending}
                  {...field("serviceTags")}
                />

                <Switch
                  className="sm:col-span-2"
                  isSelected={values.isActive}
                  isReadOnly={pending}
                  onChange={(selected) => setField("isActive", selected)}
                >
                  <Switch.Content>
                    <Switch.Control>
                      <Switch.Thumb />
                    </Switch.Control>
                    {copy.fields.active}
                  </Switch.Content>
                </Switch>
              </div>

              {noChanges && (
                <Alert status="accent" className="mt-4">
                  <Alert.Indicator aria-hidden="true" />
                  <Alert.Content>
                    <Alert.Description>{copy.noChanges}</Alert.Description>
                  </Alert.Content>
                </Alert>
              )}
              {error && <ErrorState error={error} codes={LOCATION_ERROR_CODES} fallback={copy.saveFailed} />}
            </Drawer.Body>
            <Drawer.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={close}>
                {copy.cancel}
              </Button>
              <Button type="submit" variant="primary" isPending={pending}>
                {copy.save}
              </Button>
            </Drawer.Footer>
          </form>
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  );
}
