// AgentBoard icon kit (sprite at /public/agentboard-icons.svg, 24x24 viewBox).
// usage: <Icon name="add-task" size={16} aria-label="Add task"/>
// Decorative icons omit aria-label → rendered with aria-hidden so screen
// readers skip the duplicate label.

type IconProps = {
  name: string;
  size?: number;
  className?: string;
  "aria-label"?: string;
};

export function Icon({
  name,
  size = 24,
  className,
  "aria-label": ariaLabel,
}: IconProps) {
  const decorative = !ariaLabel;
  return (
    <svg
      width={size}
      height={size}
      className={className}
      aria-label={ariaLabel}
      aria-hidden={decorative}
      role={decorative ? undefined : "img"}
    >
      <use href={`/agentboard-icons.svg#icon-${name}`} />
    </svg>
  );
}
