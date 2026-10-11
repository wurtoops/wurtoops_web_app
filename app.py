require 'json'

module PattyEngine
  def self.construir
    model = Sketchup.active_model
    path = UI.openpanel("Seleccionar JSON Patty", "", "JSON|*.json;||")
    return unless path && File.exist?(path)
    
    dir = File.dirname(path)
    data = JSON.parse(File.read(path))
    
    model.start_operation("Patty Automático", true)
    begin
      model.entities.erase_entities(model.entities.to_a)
      model.definitions.purge_unused
      
      ly_muros = model.layers.add("PATTY_MUROS")
      ly_piso = model.layers.add("PATTY_PISOS")
      ly_vanos = model.layers.add("PATTY_VANOS")
      ly_muebles = model.layers.add("PATTY_MUEBLES")
      
      h_muro = (data['altura_muros'] || 2.8).to_f.m
      
      # MUROS
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
      
      # PISO
      if data['contorno_piso']
        g_piso = model.entities.add_group
        g_piso.layer = ly_piso
        face = g_piso.entities.add_face(sanear(data['contorno_piso']))
        if face
          face.reverse! if face.normal.z < 0
          face.pushpull(-0.05.m)
        end
      end
      
      # VANOS, DINTELES AL RAS Y PUERTAS
      g_vanos = model.entities.add_group
      g_vanos.layer = ly_vanos
      (data['vanos'] || []).each do |v|
        p1 = Geom::Point3d.new(v['p1']['x'].to_f.m, v['p1']['y'].to_f.m, 0)
        p2 = Geom::Point3d.new(v['p2']['x'].to_f.m, v['p2']['y'].to_f.m, 0)
        
        dx, dy = p2.x - p1.x, p2.y - p1.y
        len = Math.hypot(dx, dy)
        next if len < 0.3.m
        
        nx, ny = -dy/len, dx/len
        
        # Grosor automático inferido por Python
        espesor = (v['espesor'] || 0.15).to_f.m 
        off_x, off_y = nx * (espesor / 2.0), ny * (espesor / 2.0)
        
        # Dintel Centrado (Al coincidir con el grosor de la pared, queda liso de ambos lados)
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
        
        # Puerta
        skp = File.join(dir, "puerta.skp")
        tr_rot = Geom::Transformation.rotation(Geom::Point3d.new(0,0,0), Geom::Vector3d.new(0,0,1), v['rot'].to_f.degrees)
        tr_pos = Geom::Transformation.translation(Geom::Vector3d.new(p1.x, p1.y, 0))
        tr_final = tr_pos * tr_rot
        
        if File.exist?(skp)
          inst = g_vanos.entities.add_instance(model.definitions.load(skp), tr_final)
          min_z = inst.bounds.min.z
          inst.transform!(Geom::Transformation.translation(Geom::Vector3d.new(0, 0, -min_z))) if min_z.abs > 0.01.m
        end
      end
      
      # MUEBLES
      g_muebles = model.entities.add_group
      g_muebles.layer = ly_muebles
      (data['muebles'] || []).each do |m|
        skp = File.join(dir, m['id'])
        tr_pos = Geom::Transformation.translation(Geom::Vector3d.new(m['x'].to_f.m, m['y'].to_f.m, 0))
        
        if File.exist?(skp)
          inst = g_muebles.entities.add_instance(model.definitions.load(skp), tr_pos)
          min_z = inst.bounds.min.z
          inst.transform!(Geom::Transformation.translation(Geom::Vector3d.new(0, 0, -min_z))) if min_z.abs > 0.01.m
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
end

PattyEngine.construir
