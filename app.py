require 'json'

module PattyEngine
  def self.construir
    model = Sketchup.active_model
    path = UI.openpanel("Seleccionar JSON Patty v2.4", "", "JSON|*.json;||")
    return unless path && File.exist?(path)
    
    dir = File.dirname(path)
    data = JSON.parse(File.read(path))
    
    model.start_operation("Patty v2.4", true)
    begin
      model.entities.erase_entities(model.entities.to_a)
      model.definitions.purge_unused
      
      ly_muros = model.layers.add("PATTY_MUROS")
      ly_piso = model.layers.add("PATTY_PISOS")
      ly_vanos = model.layers.add("PATTY_VANOS")
      ly_muebles = model.layers.add("PATTY_MUEBLES")
      
      h_muro = (data['altura_muros'] || 2.8).to_f.m
      
      # 1. MUROS
      g_muros = model.entities.add_group
      g_muros.layer = ly_muros
      data['muros'].each do |pts|
        limpios = sanear(pts)
        next if limpios.size < 3
        face = g_muros.entities.add_face(limpios)
        if face
          face.reverse! if face.normal.z < 0
          face.pushpull(h_muro)
        end
      end
      
      # 2. PISO
      if data['contorno_piso']
        g_piso = model.entities.add_group
        g_piso.layer = ly_piso
        face = g_piso.entities.add_face(sanear(data['contorno_piso']))
        if face
          face.reverse! if face.normal.z < 0
          face.pushpull(-0.05.m)
        end
      end
      
      # 3. VANOS, DINTELES Y PUERTAS
      g_vanos = model.entities.add_group
      g_vanos.layer = ly_vanos
      (data['vanos'] || []).each do |v|
        p1 = Geom::Point3d.new(v['p1']['x'].to_f.m, v['p1']['y'].to_f.m, 0)
        p2 = Geom::Point3d.new(v['p2']['x'].to_f.m, v['p2']['y'].to_f.m, 0)
        
        dx, dy = p2.x - p1.x, p2.y - p1.y
        len = Math.hypot(dx, dy)
        next if len < 0.3.m
        
        nx, ny = -dy/len, dx/len
        off_x, off_y = nx * 0.10.m, ny * 0.10.m
        
        # Dintel
        h_d = h_muro - 2.10.m
        pts_d = [
          Geom::Point3d.new(p1.x+off_x, p1.y+off_y, 2.1.m),
          Geom::Point3d.new(p2.x+off_x, p2.y+off_y, 2.1.m),
          Geom::Point3d.new(p2.x-off_x, p2.y-off_y, 2.1.m),
          Geom::Point3d.new(p1.x-off_x, p1.y-off_y, 2.1.m)
        ]
        face_d = g_vanos.entities.add_face(pts_d)
        if face_d
          face_d.reverse! if face_d.normal.z < 0
          face_d.pushpull(h_d)
        end
        
        # Puerta (Anclada en P1 para usar la bisagra de tu modelo)
        if v['tipo'] == 'puerta'
          skp = File.join(dir, "puerta.skp")
          tr_rot = Geom::Transformation.rotation(Geom::Point3d.new(0,0,0), Geom::Vector3d.new(0,0,1), v['rot'].to_f.degrees)
          tr_pos = Geom::Transformation.translation(Geom::Vector3d.new(p1.x, p1.y, 0))
          tr_final = tr_pos * tr_rot
          
          if File.exist?(skp)
            inst = g_vanos.entities.add_instance(model.definitions.load(skp), tr_final)
            min_z = inst.bounds.min.z
            if min_z.abs > 0.01.m
              inst.transform!(Geom::Transformation.translation(Geom::Vector3d.new(0, 0, -min_z)))
            end
          else
            # ALERTA: Si no encuentra el archivo, dibuja un proxy para que sepas que el motor funciona
            puts "[Patty] ALERTA: No se encontró puerta.skp en la carpeta #{dir}"
            crear_proxy_puerta(g_vanos.entities, tr_final, len)
          end
        end
      end
      
      # 4. MUEBLES
      g_muebles = model.entities.add_group
      g_muebles.layer = ly_muebles
      (data['muebles'] || []).each do |m|
        skp = File.join(dir, m['id'])
        tr_pos = Geom::Transformation.translation(Geom::Vector3d.new(m['x'].to_f.m, m['y'].to_f.m, 0))
        
        if File.exist?(skp)
          inst = g_muebles.entities.add_instance(model.definitions.load(skp), tr_pos)
          min_z = inst.bounds.min.z
          if min_z.abs > 0.01.m
            inst.transform!(Geom::Transformation.translation(Geom::Vector3d.new(0, 0, -min_z)))
          end
        else
          radio = m['tipo'] == 'mesa_4p' ? 0.50.m : 0.35.m
          crear_mesa_proxy(g_muebles.entities, Geom::Point3d.new(m['x'].to_f.m, m['y'].to_f.m, 0), radio)
        end
      end
      
      model.commit_operation
      model.active_view.zoom_extents
    rescue => e
      model.abort_operation
      UI.messagebox("Error: #{e.message}")
    end
  end

  def self.sanear(pts)
    raw = pts.map { |p| Geom::Point3d.new(p['x'].to_f.m, p['y'].to_f.m, 0) }
    unq = []
    raw.each { |p| unq << p if unq.empty? || unq.last.distance(p) > 0.005.m }
    unq.pop if unq.size > 1 && unq.first.distance(unq.last) < 0.005.m
    unq
  end

  # Función de Proxy en caso de que falte el archivo puerta.skp
  def self.crear_proxy_puerta(entities, tr_final, ancho)
    grp = entities.add_group
    pts = [
      Geom::Point3d.new(0, 0, 0),
      Geom::Point3d.new(ancho, 0, 0),
      Geom::Point3d.new(ancho, 0.05.m, 0),
      Geom::Point3d.new(0, 0.05.m, 0)
    ]
    face = grp.entities.add_face(pts)
    face.pushpull(2.10.m) if face
    grp.transform!(tr_final)
  end

  def self.crear_mesa_proxy(entities, centro, radio)
    grp = entities.add_group
    lados = 16
    pts = (0...lados).map do |i|
      ang = i * (2.0 * Math::PI / lados)
      Geom::Point3d.new(centro.x + radio * Math.cos(ang), centro.y + radio * Math.sin(ang), 0)
    end
    face = grp.entities.add_face(pts)
    if face
      face.reverse! if face.normal.z < 0
      face.pushpull(0.75.m)
    end
  end
end

PattyEngine.construir
