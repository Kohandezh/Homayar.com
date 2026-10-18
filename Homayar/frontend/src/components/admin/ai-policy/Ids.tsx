/** Ids (regions, manifests) read left to right, inside Unicode isolates. */
export default function Ids({ ids }: { ids: readonly string[] }) {
  return (
    <>
      {ids.map((id, index) => (
        <span key={id}>
          {index > 0 && ", "}
          <bdi dir="ltr" className="font-mono text-xs">
            {id}
          </bdi>
        </span>
      ))}
    </>
  );
}
