(function(){
  const S = window.HME;

  function groupArg(value){ return S.inlineArg(value); }

  S.renderGroups = async function(){
    S.setTitle('分组管理');
    S.view(S.loading(4));
    try {
      const data = await S.api('/api/groups');
      S.groups = data.groups || [];
      const rows = S.groups.map((g, i) => {
        const idArg = groupArg(g.id);
        const name = S.esc(g.name || g.id);
        const color = S.esc(g.color || '#1f8b4c');
        const badge = '<span class="badge" style="border-color:' + color + '">' + name + '</span>';
        const actions = (g.is_system || g.is_default) ? '<span class="muted">内置状态分组</span>' : '<button class="btn btn-outline btn-sm" onclick="HME.showGroupModal(' + idArg + ')">编辑</button> <button class="btn btn-danger btn-sm" onclick="HME.deleteGroup(' + idArg + ')">删除</button>';
        return '<tr><td>' + (i + 1) + '</td><td>' + badge + '</td><td>' + S.esc(g.description || '') + '</td><td>' + (g.mailbox_count || 0) + '</td><td>' + (g.sort_order || 0) + '</td><td>' + actions + '</td></tr>';
      }).join('');
      const body = rows || '<tr><td colspan="6" class="muted">暂无分组</td></tr>';
      S.view('<div class="panel"><div class="panel-head"><span>邮箱分组管理</span><button class="btn btn-sm" onclick="HME.showGroupModal()">新建分组</button></div><div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>分组</th><th>说明</th><th>邮箱数</th><th>排序</th><th>操作</th></tr></thead><tbody>' + body + '</tbody></table></div></div>');
    } catch (err) {
      S.view(S.error(err, 'HME.renderGroups()'));
    }
  };

  S.showGroupModal = function(id){
    const group = id ? (S.groups || []).find(g => g.id === id) : null;
    const title = group ? '编辑分组' : '新建分组';
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> ' + title + '</h3><label class="label">分组名称</label><input id="groupNameInput" placeholder="分组名称" value="' + S.esc(group && group.name || '') + '"><label class="label">颜色</label><input id="groupColorInput" placeholder="#1f8b4c" value="' + S.esc(group && group.color || '#1f8b4c') + '"><label class="label">说明</label><textarea id="groupDescInput" placeholder="可选">' + S.esc(group && group.description || '') + '</textarea><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.saveGroup(' + groupArg(id || '') + ')">保存</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };

  S.saveGroup = async function(id){
    const payload = {
      name: S.E('groupNameInput').value.trim(),
      color: S.E('groupColorInput').value.trim(),
      description: S.E('groupDescInput').value.trim(),
    };
    if (!payload.name) { S.E('modalMsg').textContent = '分组名称不能为空'; return; }
    try {
      const path = id ? '/api/groups/' + encodeURIComponent(id) : '/api/groups';
      await S.api(path, {method:id ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      S.closeModal();
      S.toast('分组已保存');
      await S.refreshAll();
    } catch (err) {
      S.E('modalMsg').textContent = err.message;
    }
  };

  S.deleteGroup = async function(id){
    if (!confirm('确认删除该分组？分组内邮箱会移至默认分组。')) return;
    try {
      await S.api('/api/groups/' + encodeURIComponent(id), {method:'DELETE'});
      S.toast('分组已删除');
      await S.refreshAll();
    } catch (err) {
      S.toast(err.message, true);
    }
  };
})();
