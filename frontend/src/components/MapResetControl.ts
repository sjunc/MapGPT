import type { IControl } from "maplibre-gl";

/** MapLibre 기본 확대/축소 컨트롤 아래에 배치되는 원래 범위 복귀 버튼. */
export class MapResetControl implements IControl {
  private container: HTMLDivElement | null = null;
  private button: HTMLButtonElement | null = null;

  constructor(
    private readonly onReset: () => void,
    private readonly label: string,
  ) {}

  private readonly handleClick = () => this.onReset();

  onAdd(): HTMLElement {
    const container = document.createElement("div");
    container.className = "maplibregl-ctrl maplibregl-ctrl-group";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "p4-reset-view";
    button.textContent = "↺";
    button.title = this.label;
    button.setAttribute("aria-label", this.label);
    button.addEventListener("click", this.handleClick);

    container.appendChild(button);
    this.container = container;
    this.button = button;
    return container;
  }

  onRemove(): void {
    this.button?.removeEventListener("click", this.handleClick);
    this.container?.remove();
    this.button = null;
    this.container = null;
  }
}
