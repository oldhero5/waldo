import type { ImgHTMLAttributes } from "react";
import { useRenewableFrame } from "../hooks/useRenewableFrame";

type Props = Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "onError" | "onLoad"> & { frameId: string; initialUrl: string };

export default function RenewableFrameImage({ frameId, initialUrl, ...props }: Props) {
  const media = useRenewableFrame(frameId, initialUrl);
  return <>
    <img key={`${media.url}:${media.loadKey}`} {...props} src={media.url} onLoad={media.onLoad} onError={media.onError} />
    {media.loading && <span role="status">Reloading image…</span>}
    {media.error && <span role="alert" className="relative z-20 text-sm">
      {media.error} {" "}
      <button type="button" onClick={(event) => { event.stopPropagation(); media.retry(); }} aria-label="Retry image" className="underline">Retry</button>
    </span>}
  </>;
}
