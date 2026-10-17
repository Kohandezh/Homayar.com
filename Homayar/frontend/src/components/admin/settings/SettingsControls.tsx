import { useId, type ReactNode } from "react";
import { Card, Description, Radio, Switch } from "@heroui/react";

/**
 * One group of settings: a HeroUI Card rendered as a <section> landmark named
 * by its <h2>, like the overview cards.
 */
export function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <Card<"section">
      render={(props) => <section {...props} />}
      aria-labelledby={headingId}
      className="min-w-0"
    >
      <Card.Header>
        <Card.Title<"h2"> render={(props) => <h2 {...props} />} id={headingId}>
          {title}
        </Card.Title>
        {description && <Card.Description>{description}</Card.Description>}
      </Card.Header>
      <Card.Content className="flex min-w-0 flex-col gap-5">{children}</Card.Content>
    </Card>
  );
}

/**
 * An on/off setting. The help line is a sibling of `Switch.Content`, so it
 * describes the switch without becoming part of its name.
 */
export function SettingSwitch({
  label,
  help,
  isSelected,
  isDisabled,
  isReadOnly,
  onChange,
}: {
  label: string;
  help: string;
  isSelected: boolean;
  isDisabled?: boolean;
  isReadOnly?: boolean;
  onChange: (isSelected: boolean) => void;
}) {
  return (
    <Switch isSelected={isSelected} isDisabled={isDisabled} isReadOnly={isReadOnly} onChange={onChange}>
      <Switch.Content>
        <Switch.Control>
          <Switch.Thumb />
        </Switch.Control>
        {label}
      </Switch.Content>
      <Description>{help}</Description>
    </Switch>
  );
}

/** One choice of a RadioGroup, with an optional help line. */
export function OptionRadio({ value, label, help }: { value: string; label: string; help?: string }) {
  return (
    <Radio value={value}>
      <Radio.Content>
        <Radio.Control>
          <Radio.Indicator />
        </Radio.Control>
        {label}
      </Radio.Content>
      {help && <Description>{help}</Description>}
    </Radio>
  );
}
